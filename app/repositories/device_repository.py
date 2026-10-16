import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Device, Profile


class DeviceRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, device_id: uuid.UUID) -> Device | None:
        result = await self.db.execute(
            select(Device)
            .options(selectinload(Device.profile).selectinload(Profile.settings))
            .where(Device.id == device_id)
        )
        return result.scalar_one_or_none()

    async def get_by_installation_id(self, installation_id: str) -> Device | None:
        result = await self.db.execute(select(Device).where(Device.installation_id == installation_id))
        return result.scalar_one_or_none()

    async def create(
        self,
        profile_id: uuid.UUID,
        installation_id: str,
        platform: str,
        *,
        last_seen_at: datetime,
    ) -> Device:
        device = Device(
            profile_id=profile_id,
            installation_id=installation_id,
            platform=platform,
            last_seen_at=last_seen_at,
        )
        self.db.add(device)
        await self.db.flush()
        return device

    async def list_by_profile(self, profile_id: uuid.UUID) -> list[Device]:
        result = await self.db.execute(
            select(Device).where(Device.profile_id == profile_id).order_by(Device.created_at.asc())
        )
        return list(result.scalars().all())

    async def get_for_profile(
        self,
        profile_id: uuid.UUID,
        device_id: uuid.UUID,
    ) -> Device | None:
        return await self.db.scalar(
            select(Device).where(
                Device.id == device_id,
                Device.profile_id == profile_id,
            )
        )

    async def deactivate_if_active(
        self,
        profile_id: uuid.UUID,
        device_id: uuid.UUID,
        *,
        updated_at: datetime,
    ) -> bool:
        result = await self.db.execute(
            update(Device)
            .where(
                Device.id == device_id,
                Device.profile_id == profile_id,
                Device.active.is_(True),
            )
            .values(active=False, updated_at=updated_at)
            .returning(Device.id)
        )
        return result.scalar_one_or_none() is not None
