import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import ProfileActivityConsideration, ProfileActivityPreferences


class ProfileActivityPreferencesRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, profile_id: uuid.UUID) -> ProfileActivityPreferences | None:
        query = (
            select(ProfileActivityPreferences)
            .options(selectinload(ProfileActivityPreferences.considerations))
            .where(ProfileActivityPreferences.profile_id == profile_id)
            .execution_options(populate_existing=True)
        )
        return (await self.db.execute(query)).scalar_one_or_none()

    async def upsert(
        self,
        profile_id: uuid.UUID,
        values: dict[str, Any],
        preferences: ProfileActivityPreferences | None,
        considerations: list[dict[str, Any]] | None,
    ) -> ProfileActivityPreferences:
        if preferences is None:
            preferences = ProfileActivityPreferences(profile_id=profile_id, **values)
            self.db.add(preferences)
        else:
            for key, value in values.items():
                setattr(preferences, key, value)

        if considerations is not None:
            if preferences.id is not None:
                preferences.considerations.clear()
                await self.db.flush()
            preferences.considerations = [
                ProfileActivityConsideration(ordinal=ordinal, **consideration)
                for ordinal, consideration in enumerate(considerations, start=1)
            ]

        await self.db.commit()
        stored = await self.get(profile_id)
        assert stored is not None
        return stored
