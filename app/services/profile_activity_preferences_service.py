import uuid

from app.db.models import ProfileActivityPreferences
from app.repositories.profile_activity_preferences_repository import ProfileActivityPreferencesRepository
from app.schemas.profile_activity_preferences import (
    ProfileActivityPreferencesRead,
    ProfileActivityPreferencesUpdate,
)


class ProfileActivityPreferencesService:
    def __init__(self, repository: ProfileActivityPreferencesRepository):
        self.repository = repository

    async def get(self, profile_id: uuid.UUID) -> ProfileActivityPreferencesRead:
        preferences = await self.repository.get(profile_id)
        if preferences is None:
            return ProfileActivityPreferencesRead(profile_id=profile_id)
        return ProfileActivityPreferencesRead.model_validate(preferences)

    async def upsert(
        self,
        profile_id: uuid.UUID,
        data: ProfileActivityPreferencesUpdate,
    ) -> ProfileActivityPreferencesRead:
        preferences = await self.repository.get(profile_id)
        values = data.model_dump(exclude_unset=preferences is not None)
        considerations = values.pop("considerations", None)
        if values.get("considerations_answered") is False:
            considerations = []
        stored = await self.repository.upsert(
            profile_id,
            values,
            preferences,
            considerations,
        )
        return ProfileActivityPreferencesRead.model_validate(stored)
