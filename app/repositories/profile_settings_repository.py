import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProfileSettings
from app.schemas.profile_settings import ProfileSettingsUpdate


class ProfileSettingsRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, profile_id: uuid.UUID) -> ProfileSettings | None:
        result = await self.db.execute(select(ProfileSettings).where(ProfileSettings.profile_id == profile_id))
        return result.scalar_one_or_none()

    async def upsert(self, profile_id: uuid.UUID, data: ProfileSettingsUpdate) -> ProfileSettings:
        settings = await self.get(profile_id)
        values = data.model_dump()
        if settings is None:
            settings = ProfileSettings(profile_id=profile_id, **values)
            self.db.add(settings)
        else:
            for key, value in values.items():
                setattr(settings, key, value)
        await self.db.commit()
        await self.db.refresh(settings)
        return settings
