import hashlib
import hmac
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Device, DeviceBootstrapToken, Profile
from app.repositories.device_bootstrap_token_repository import DeviceBootstrapTokenRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.profile_repository import ProfileRepository
from app.schemas.device import DeviceCreate
from app.schemas.profile import ProfileCreate
from app.services.device_auth_service import DeviceAuthService, DeviceTokenResult
from app.services.device_service import (
    DeviceAlreadyLinkedToAnotherProfileError,
    DeviceService,
    InactiveProfileError,
    is_installation_id_conflict,
)


class InvalidBootstrapTokenError(Exception):
    pass


class BootstrapProfileMismatchError(Exception):
    pass


class BootstrapGenerationError(Exception):
    pass


@dataclass(frozen=True)
class ProfileBootstrapResult:
    profile: Profile
    bootstrap_token: str
    expires_at: datetime


@dataclass(frozen=True)
class BootstrapRegistrationResult:
    device: Device
    token: DeviceTokenResult


def generate_bootstrap_token() -> str:
    return secrets.token_urlsafe(32)


def is_bootstrap_hash_conflict(exc: IntegrityError) -> bool:
    constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    return constraint_name == "uq_device_bootstrap_tokens_hash" or (
        "device_bootstrap_tokens.token_hash" in str(exc.orig)
        and "unique" in str(exc.orig).lower()
    )


class DeviceBootstrapService:
    def __init__(
        self,
        db: AsyncSession,
        hash_secret: str,
        ttl_minutes: int,
        device_auth: DeviceAuthService,
        token_generator: Callable[[], str] = generate_bootstrap_token,
    ):
        self.db = db
        self.hash_secret = hash_secret.encode("utf-8")
        self.ttl = timedelta(minutes=ttl_minutes)
        self.device_auth = device_auth
        self.token_generator = token_generator
        self.bootstrap_tokens = DeviceBootstrapTokenRepository(db)
        self.profiles = ProfileRepository(db)
        self.devices = DeviceRepository(db)
        self.device_service = DeviceService(db, self.devices)

    def hash_token(self, token: str) -> str:
        message = f"device-bootstrap:{token}".encode("utf-8")
        return hmac.new(self.hash_secret, message, hashlib.sha256).hexdigest()

    async def create_profile(self, data: ProfileCreate) -> ProfileBootstrapResult:
        now = datetime.now(timezone.utc)
        profile = await self.profiles.create(data, commit=False)

        for _ in range(100):
            token = self.token_generator()
            token_hash = self.hash_token(token)
            if await self.bootstrap_tokens.token_hash_exists(token_hash):
                continue
            try:
                async with self.db.begin_nested():
                    await self.bootstrap_tokens.create(
                        profile.id,
                        token_hash,
                        now + self.ttl,
                    )
            except IntegrityError as exc:
                if is_bootstrap_hash_conflict(exc):
                    continue
                await self.db.rollback()
                raise

            await self.db.commit()
            persisted = await self.profiles.get(profile.id)
            if persisted is None:
                raise RuntimeError("Created profile could not be reloaded")
            return ProfileBootstrapResult(persisted, token, now + self.ttl)

        await self.db.rollback()
        raise BootstrapGenerationError

    async def register_device(
        self,
        profile_id: uuid.UUID,
        raw_token: str,
        data: DeviceCreate,
    ) -> BootstrapRegistrationResult:
        now = datetime.now(timezone.utc)
        token_hash = self.hash_token(raw_token)

        try:
            bootstrap = await self.bootstrap_tokens.get_for_registration(token_hash)
            self._validate_bootstrap(bootstrap, token_hash, now)
            if bootstrap.profile_id != profile_id:  # type: ignore[union-attr]
                raise BootstrapProfileMismatchError

            profile = await self.profiles.get(profile_id)
            if profile is None:
                raise InvalidBootstrapTokenError
            if not profile.active:
                raise InactiveProfileError

            registration = await self.device_service.ensure_link(profile, data, seen_at=now)
            consumed = await self.bootstrap_tokens.mark_used_if_valid(
                bootstrap.id,  # type: ignore[union-attr]
                profile_id,
                now,
            )
            if not consumed:
                raise InvalidBootstrapTokenError

            await self.db.commit()
            return BootstrapRegistrationResult(
                device=registration.device,
                token=self.device_auth.issue(registration.device),
            )
        except IntegrityError as exc:
            await self.db.rollback()
            if is_installation_id_conflict(exc):
                raise DeviceAlreadyLinkedToAnotherProfileError from exc
            raise
        except Exception:
            await self.db.rollback()
            raise

    @staticmethod
    def _validate_bootstrap(
        bootstrap: DeviceBootstrapToken | None,
        expected_hash: str,
        now: datetime,
    ) -> None:
        if bootstrap is None or not hmac.compare_digest(bootstrap.token_hash, expected_hash):
            raise InvalidBootstrapTokenError

        expires_at = bootstrap.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if (
            bootstrap.used_at is not None
            or bootstrap.revoked_at is not None
            or now >= expires_at
        ):
            raise InvalidBootstrapTokenError
