import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AdultAccessTokenCodec, InvalidAccessTokenError
from app.db.models import AdultAccount, AdultOtpChallenge
from app.repositories.adult_account_repository import AdultAccountRepository
from app.repositories.adult_otp_repository import AdultOtpRepository
from app.schemas.adult_auth import normalize_email
from app.services.otp_delivery_service import OtpDeliveryService
from app.services.otp_rate_limiter import OtpRateLimiter


class InvalidOtpError(Exception):
    pass


@dataclass(frozen=True)
class OtpRequestResult:
    dev_code: str | None


@dataclass(frozen=True)
class AdultTokenResult:
    access_token: str
    expires_in: int


def generate_otp_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def is_adult_email_conflict(exc: IntegrityError) -> bool:
    constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    return constraint_name == "uq_adult_accounts_email" or (
        "adult_accounts.email" in str(exc.orig) and "unique" in str(exc.orig).lower()
    )


class AdultAuthService:
    def __init__(
        self,
        db: AsyncSession,
        jwt_secret: str,
        jwt_expiration_minutes: int,
        otp_ttl_minutes: int,
        otp_max_attempts: int,
        delivery: OtpDeliveryService,
        rate_limiter: OtpRateLimiter,
        *,
        expose_dev_otp: bool = False,
    ):
        self.db = db
        self.hash_secret = jwt_secret.encode("utf-8")
        self.token_codec = AdultAccessTokenCodec(jwt_secret, jwt_expiration_minutes)
        self.otp_ttl = timedelta(minutes=otp_ttl_minutes)
        self.otp_max_attempts = otp_max_attempts
        self.delivery = delivery
        self.rate_limiter = rate_limiter
        self.expose_dev_otp = expose_dev_otp
        self.adults = AdultAccountRepository(db)
        self.challenges = AdultOtpRepository(db)

    async def request_otp(self, email: str, client_ip: str) -> OtpRequestResult:
        normalized_email = normalize_email(email)
        await self.rate_limiter.enforce("request", normalized_email, client_ip)
        adult = await self.adults.get_by_email(normalized_email)
        if adult is None:
            try:
                async with self.db.begin_nested():
                    adult = await self.adults.create(normalized_email)
            except IntegrityError as exc:
                if not is_adult_email_conflict(exc):
                    await self.db.rollback()
                    raise
                adult = await self.adults.get_by_email(normalized_email)
                if adult is None:
                    await self.db.rollback()
                    raise

        code = generate_otp_code()
        challenge_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        await self.challenges.create(
            challenge_id,
            adult.id,
            self._hash_otp(challenge_id, code),
            now + self.otp_ttl,
        )
        try:
            await self.delivery.deliver(normalized_email, code)
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise
        return OtpRequestResult(dev_code=code if self.expose_dev_otp else None)

    async def verify_otp(self, email: str, code: str, client_ip: str) -> AdultTokenResult:
        normalized_email = normalize_email(email)
        await self.rate_limiter.enforce("verify", normalized_email, client_ip)
        now = datetime.now(timezone.utc)
        adult = await self.adults.get_by_email(normalized_email)
        if adult is None or not adult.active:
            await self.db.rollback()
            raise InvalidOtpError

        challenge = await self.challenges.get_latest_for_verification(adult.id)
        if not self._challenge_can_be_used(challenge, now):
            await self.db.rollback()
            raise InvalidOtpError

        supplied_hash = self._hash_otp(challenge.id, code)  # type: ignore[union-attr]
        if not hmac.compare_digest(challenge.code_hash, supplied_hash):  # type: ignore[union-attr]
            challenge.attempts += 1  # type: ignore[union-attr]
            await self.db.commit()
            raise InvalidOtpError

        challenge.consumed_at = now  # type: ignore[union-attr]
        adult.last_login_at = now
        await self.db.commit()
        token, expires_in = self.token_codec.issue(adult.id, now=now)
        return AdultTokenResult(access_token=token, expires_in=expires_in)

    async def authenticate(self, token: str) -> AdultAccount:
        try:
            adult_id = self.token_codec.decode_subject(token)
        except InvalidAccessTokenError:
            raise
        adult = await self.adults.get(adult_id)
        if adult is None or not adult.active:
            raise InvalidAccessTokenError
        return adult

    def _hash_otp(self, challenge_id: uuid.UUID, code: str) -> str:
        message = f"adult-otp:{challenge_id}:{code}".encode("ascii")
        return hmac.new(self.hash_secret, message, hashlib.sha256).hexdigest()

    def _challenge_can_be_used(
        self,
        challenge: AdultOtpChallenge | None,
        now: datetime,
    ) -> bool:
        if challenge is None:
            return False
        expires_at = challenge.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return (
            challenge.consumed_at is None
            and challenge.attempts < self.otp_max_attempts
            and now < expires_at
        )
