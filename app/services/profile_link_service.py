import hashlib
import hmac
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdultAccount, AdultProfileLink, Device, Profile, ProfileLinkCode
from app.repositories.adult_profile_link_repository import AdultProfileLinkRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.profile_link_code_repository import ProfileLinkCodeRepository
from app.repositories.profile_repository import ProfileRepository
from app.repositories.representative_verification_repository import RepresentativeVerificationRepository
from app.schemas.device import DeviceCreate
from app.schemas.profile_link import (
    AdultProfileLinkClaimRequest,
    AdultProfileLinkCodeRead,
    DeviceLinkClaimRequest,
    DeviceLinkCodeRead,
)
from app.services.device_service import (
    DeviceAlreadyLinkedToAnotherProfileError,
    DeviceService,
    InactiveProfileError,
    is_installation_id_conflict,
)

DEVICE_LINK_PURPOSE = "DEVICE_LINK"
ADULT_PROFILE_LINK_PURPOSE = "ADULT_PROFILE_LINK"


class InvalidLinkCodeError(Exception):
    pass


class LinkCodeGenerationError(Exception):
    pass


class RevokedAdultProfileLinkError(Exception):
    pass


def generate_numeric_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def is_code_hash_conflict(exc: IntegrityError) -> bool:
    constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    return constraint_name == "uq_profile_link_codes_code_hash" or (
        "profile_link_codes.code_hash" in str(exc.orig) and "unique" in str(exc.orig).lower()
    )


def is_adult_profile_link_conflict(exc: IntegrityError) -> bool:
    constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    return constraint_name == "uq_adult_profile_links_adult_profile" or (
        "adult_profile_links.adult_id" in str(exc.orig)
        and "adult_profile_links.profile_id" in str(exc.orig)
        and "unique" in str(exc.orig).lower()
    )


class ProfileLinkService:
    def __init__(
        self,
        db: AsyncSession,
        hash_secret: str,
        ttl_minutes: int = 10,
        code_generator: Callable[[], str] = generate_numeric_code,
    ):
        self.db = db
        self.hash_secret = hash_secret.encode("utf-8")
        self.ttl = timedelta(minutes=ttl_minutes)
        self.code_generator = code_generator
        self.link_codes = ProfileLinkCodeRepository(db)
        self.profiles = ProfileRepository(db)
        self.devices = DeviceRepository(db)
        self.adult_links = AdultProfileLinkRepository(db)
        self.verifications = RepresentativeVerificationRepository(db)
        self.device_service = DeviceService(db, self.devices)

    def hash_code(self, code: str) -> str:
        return hmac.new(self.hash_secret, code.encode("ascii"), hashlib.sha256).hexdigest()

    async def generate(self, profile: Profile) -> DeviceLinkCodeRead:
        code, expires_at = await self._generate(profile, DEVICE_LINK_PURPOSE)
        return DeviceLinkCodeRead(
            code=code,
            expires_at=expires_at,
            purpose=DEVICE_LINK_PURPOSE,
        )

    async def generate_adult_profile_link_code(self, profile: Profile) -> AdultProfileLinkCodeRead:
        code, expires_at = await self._generate(profile, ADULT_PROFILE_LINK_PURPOSE)
        return AdultProfileLinkCodeRead(
            code=code,
            expires_at=expires_at,
            purpose=ADULT_PROFILE_LINK_PURPOSE,
        )

    async def _generate(self, profile: Profile, purpose: str) -> tuple[str, datetime]:
        if not profile.active:
            raise InactiveProfileError

        now = datetime.now(timezone.utc)
        for _ in range(100):
            code = self.code_generator()
            code_hash = self.hash_code(code)
            if await self.link_codes.code_hash_exists(code_hash):
                continue

            try:
                async with self.db.begin_nested():
                    await self.link_codes.create(
                        profile.id,
                        code_hash,
                        purpose,
                        now + self.ttl,
                    )
            except IntegrityError as exc:
                if is_code_hash_conflict(exc):
                    continue
                await self.db.rollback()
                raise

            await self.db.commit()
            return code, now + self.ttl

        await self.db.rollback()
        raise LinkCodeGenerationError

    async def claim(self, data: DeviceLinkClaimRequest) -> tuple[Device, Profile]:
        return await self._claim(data, retry_on_device_conflict=True)

    async def _claim(
        self,
        data: DeviceLinkClaimRequest,
        *,
        retry_on_device_conflict: bool,
    ) -> tuple[Device, Profile]:
        now = datetime.now(timezone.utc)
        code_hash = self.hash_code(data.code)

        try:
            link_code = await self.link_codes.get_for_claim(code_hash)
            self._validate_link_code(link_code, code_hash, DEVICE_LINK_PURPOSE, now)

            profile = await self.profiles.get(link_code.profile_id)  # type: ignore[union-attr]
            if profile is None or not profile.active:
                raise InvalidLinkCodeError

            registration = await self.device_service.ensure_link(
                profile,
                DeviceCreate(installation_id=data.installation_id, platform=data.platform),
                seen_at=now,
            )
            consumed = await self.link_codes.mark_used_if_valid(
                link_code.id,  # type: ignore[union-attr]
                DEVICE_LINK_PURPOSE,
                now,
            )
            if not consumed:
                raise InvalidLinkCodeError

            await self.db.commit()
            return registration.device, profile
        except IntegrityError as exc:
            await self.db.rollback()
            if retry_on_device_conflict and is_installation_id_conflict(exc):
                return await self._claim(data, retry_on_device_conflict=False)
            raise
        except Exception:
            await self.db.rollback()
            raise

    async def claim_adult_profile_link(
        self,
        adult: AdultAccount,
        data: AdultProfileLinkClaimRequest,
    ) -> tuple[AdultProfileLink, Profile]:
        return await self._claim_adult_profile_link(adult, data, retry_on_link_conflict=True)

    async def _claim_adult_profile_link(
        self,
        adult: AdultAccount,
        data: AdultProfileLinkClaimRequest,
        *,
        retry_on_link_conflict: bool,
    ) -> tuple[AdultProfileLink, Profile]:
        now = datetime.now(timezone.utc)
        code_hash = self.hash_code(data.code)

        try:
            link_code = await self.link_codes.get_for_claim(code_hash)
            self._validate_link_code(
                link_code,
                code_hash,
                ADULT_PROFILE_LINK_PURPOSE,
                now,
            )

            profile = await self.profiles.get(link_code.profile_id)  # type: ignore[union-attr]
            if profile is None or not profile.active:
                raise InvalidLinkCodeError

            adult_link = await self.adult_links.get_link_for_update(adult.id, profile.id)
            if adult_link is None:
                adult_link = await self.adult_links.create_link(adult.id, profile.id)
            elif not adult_link.active or adult_link.revoked_at is not None:
                raise RevokedAdultProfileLinkError

            verification = await self.verifications.get_by_adult_profile(adult.id, profile.id)
            if verification is None:
                await self.verifications.create_pending(adult.id, profile.id)

            consumed = await self.link_codes.mark_used_if_valid(
                link_code.id,  # type: ignore[union-attr]
                ADULT_PROFILE_LINK_PURPOSE,
                now,
            )
            if not consumed:
                raise InvalidLinkCodeError

            await self.db.commit()
            return adult_link, profile
        except IntegrityError as exc:
            await self.db.rollback()
            if retry_on_link_conflict and is_adult_profile_link_conflict(exc):
                return await self._claim_adult_profile_link(
                    adult,
                    data,
                    retry_on_link_conflict=False,
                )
            raise
        except Exception:
            await self.db.rollback()
            raise

    @staticmethod
    def _validate_link_code(
        link_code: ProfileLinkCode | None,
        expected_hash: str,
        expected_purpose: str,
        now: datetime,
    ) -> None:
        if link_code is None or not hmac.compare_digest(link_code.code_hash, expected_hash):
            raise InvalidLinkCodeError

        expires_at = link_code.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if (
            link_code.purpose != expected_purpose
            or link_code.used_at is not None
            or link_code.revoked_at is not None
            or now >= expires_at
        ):
            raise InvalidLinkCodeError
