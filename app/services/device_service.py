from dataclasses import dataclass
from datetime import datetime, timezone
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdultAccount, AuditActorType, AuditEventType, Device, Profile
from app.repositories.audit_event_repository import AuditEventRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.profile_link_code_repository import ProfileLinkCodeRepository
from app.schemas.device import DeviceCreate


class DeviceAlreadyLinkedToAnotherProfileError(Exception):
    pass


class InactiveProfileError(Exception):
    pass


class DeviceNotFoundError(Exception):
    pass


@dataclass(frozen=True)
class DeviceRegistration:
    device: Device
    created: bool


def is_installation_id_conflict(exc: IntegrityError) -> bool:
    constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    return constraint_name == "uq_devices_installation_id" or (
        "devices.installation_id" in str(exc.orig) and "unique" in str(exc.orig).lower()
    )


class DeviceService:
    def __init__(self, db: AsyncSession, repository: DeviceRepository | None = None):
        self.db = db
        self.repository = repository or DeviceRepository(db)
        self.link_codes = ProfileLinkCodeRepository(db)
        self.audit = AuditEventRepository(db)

    async def list_for_profile(self, profile_id: uuid.UUID) -> list[Device]:
        return await self.repository.list_by_profile(profile_id)

    async def deactivate(
        self,
        profile_id: uuid.UUID,
        device_id: uuid.UUID,
        actor: AdultAccount,
    ) -> Device:
        now = datetime.now(timezone.utc)
        try:
            changed = await self.repository.deactivate_if_active(
                profile_id,
                device_id,
                updated_at=now,
            )
            device = await self.repository.get_for_profile(profile_id, device_id)
            if device is None:
                await self.db.rollback()
                raise DeviceNotFoundError

            if changed:
                revoked_codes = await self.link_codes.revoke_pending_for_profile(
                    profile_id,
                    "ADULT_PROFILE_LINK",
                    now,
                )
                await self.audit.append(
                    actor_type=AuditActorType.ADULT,
                    actor_id=actor.id,
                    profile_id=profile_id,
                    event_type=AuditEventType.DEVICE_DEACTIVATED,
                    metadata={
                        "device_id": str(device_id),
                        "revoked_pending_adult_link_codes": revoked_codes,
                    },
                )
            await self.db.commit()
            return device
        except DeviceNotFoundError:
            raise
        except Exception:
            await self.db.rollback()
            raise

    async def ensure_link(
        self,
        profile: Profile,
        data: DeviceCreate,
        *,
        seen_at: datetime | None = None,
    ) -> DeviceRegistration:
        if not profile.active:
            raise InactiveProfileError

        existing = await self.repository.get_by_installation_id(data.installation_id)
        if existing is not None:
            if existing.profile_id != profile.id:
                raise DeviceAlreadyLinkedToAnotherProfileError
            return DeviceRegistration(device=existing, created=False)

        device = await self.repository.create(
            profile.id,
            data.installation_id,
            data.platform,
            last_seen_at=seen_at or datetime.now(timezone.utc),
        )
        return DeviceRegistration(device=device, created=True)

    async def register(self, profile: Profile, data: DeviceCreate) -> DeviceRegistration:
        try:
            registration = await self.ensure_link(profile, data)
            await self.db.commit()
            return registration
        except IntegrityError as exc:
            await self.db.rollback()
            if not is_installation_id_conflict(exc):
                raise

            existing = await self.repository.get_by_installation_id(data.installation_id)
            if existing is None:
                raise
            if existing.profile_id != profile.id:
                raise DeviceAlreadyLinkedToAnotherProfileError from exc
            return DeviceRegistration(device=existing, created=False)
        except Exception:
            await self.db.rollback()
            raise
