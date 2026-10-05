from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Device, Profile
from app.repositories.device_repository import DeviceRepository
from app.schemas.device import DeviceCreate


class DeviceAlreadyLinkedToAnotherProfileError(Exception):
    pass


class InactiveProfileError(Exception):
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
