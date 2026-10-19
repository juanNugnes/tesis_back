import hashlib
import hmac
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import OtpRateLimitEvent


class OtpRateLimitExceeded(Exception):
    def __init__(self, retry_after: int):
        self.retry_after = retry_after


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PersistentRateLimiter:
    def __init__(self, db: AsyncSession, secret: str, settings: Settings):
        self.db = db
        self.secret = secret.encode("utf-8")
        self.retention = timedelta(minutes=max(
            settings.otp_rate_window_minutes, settings.link_rate_window_minutes
        ))

    def _key(self, scope: str, value: str) -> str:
        return hmac.new(
            self.secret, f"rate-limit:{scope}:{value}".encode("utf-8"), hashlib.sha256
        ).hexdigest()

    async def enforce_scopes(
        self, dimensions: tuple[tuple[str, str, int], ...], window: timedelta,
        cooldown_scope: str | None = None, cooldown: timedelta | None = None,
    ) -> None:
        keys = tuple((scope, self._key(scope, value), limit)
                     for scope, value, limit in dimensions)
        try:
            dialect = self.db.bind.dialect.name
            if dialect == "postgresql":
                # A stable lock order prevents deadlocks between overlapping dimensions.
                lock_ids = sorted({int.from_bytes(bytes.fromhex(key)[:8], "big", signed=True)
                                   for _, key, _ in keys})
                for lock_id in lock_ids:
                    await self.db.execute(
                        text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id}
                    )
            elif dialect == "sqlite":
                # Test/local SQLite has no advisory locks; acquire its write lock up front.
                await self.db.execute(text("BEGIN IMMEDIATE"))
            else:
                raise RuntimeError("Rate limiting requires PostgreSQL or SQLite")

            now = utc_now()
            retry_until = now
            for scope, key_hash, limit in keys:
                times = list((await self.db.scalars(
                    select(OtpRateLimitEvent.occurred_at)
                    .where(
                        OtpRateLimitEvent.scope == scope,
                        OtpRateLimitEvent.key_hash == key_hash,
                        OtpRateLimitEvent.occurred_at > now - window,
                    )
                    .order_by(OtpRateLimitEvent.occurred_at)
                )).all())
                times = [value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
                         for value in times]
                if len(times) >= limit:
                    retry_until = max(retry_until, times[0] + window)
                if scope == cooldown_scope and times and cooldown is not None:
                    retry_until = max(retry_until, times[-1] + cooldown)

            if retry_until > now:
                raise OtpRateLimitExceeded(max(1, math.ceil((retry_until - now).total_seconds())))

            self.db.add_all([
                OtpRateLimitEvent(scope=scope, key_hash=key_hash, occurred_at=now)
                for scope, key_hash, _ in keys
            ])
            await self.db.execute(
                delete(OtpRateLimitEvent).where(
                    OtpRateLimitEvent.occurred_at <= now - self.retention
                )
            )
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise


class OtpRateLimiter(PersistentRateLimiter):
    def __init__(self, db: AsyncSession, secret: str, settings: Settings):
        super().__init__(db, secret, settings)
        self.window = timedelta(minutes=settings.otp_rate_window_minutes)
        self.request_email_limit = settings.otp_request_email_limit
        self.request_ip_limit = settings.otp_request_ip_limit
        self.cooldown = timedelta(seconds=settings.otp_request_cooldown_seconds)
        self.verify_email_limit = settings.otp_verify_email_limit
        self.verify_ip_limit = settings.otp_verify_ip_limit

    def _key(self, scope: str, value: str) -> str:
        # Preserve existing OTP buckets across deployment of link-code limits.
        return hmac.new(
            self.secret, f"otp-rate-limit:{scope}:{value}".encode("utf-8"), hashlib.sha256
        ).hexdigest()

    async def enforce(self, action: str, email: str, client_ip: str) -> None:
        if action == "request":
            limits = (self.request_email_limit, self.request_ip_limit)
        elif action == "verify":
            limits = (self.verify_email_limit, self.verify_ip_limit)
        else:
            raise ValueError("Unsupported OTP rate limit action")

        await self.enforce_scopes(
            ((f"{action}_email", email, limits[0]),
             (f"{action}_ip", client_ip, limits[1])),
            self.window,
            cooldown_scope="request_email" if action == "request" else None,
            cooldown=self.cooldown if action == "request" else None,
        )
