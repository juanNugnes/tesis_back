import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProfileActivityPreferences


class ProfileActivityPreferencesRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, profile_id: uuid.UUID) -> ProfileActivityPreferences | None:
        query = select(ProfileActivityPreferences).where(ProfileActivityPreferences.profile_id == profile_id)
        return (await self.db.execute(query)).scalar_one_or_none()

    async def upsert(
        self,
        profile_id: uuid.UUID,
        values: dict[str, Any],
        preferences: ProfileActivityPreferences | None,
    ) -> ProfileActivityPreferences:
        if preferences is None:
            preferences = ProfileActivityPreferences(profile_id=profile_id, **values)
            self.db.add(preferences)
        else:
            for key, value in values.items():
                setattr(preferences, key, value)
        await self.db.commit()
        await self.db.refresh(preferences)
        return preferences
