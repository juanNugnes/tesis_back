import uuid

from app.db.models import AdaptationType, ProfileActivityPreferences
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
    ) -> ProfileActivityPreferences:
        preferences = await self.repository.get(profile_id)
        values = data.model_dump(exclude_unset=preferences is not None)
        current_adaptation = values.get(
            "adaptation_type",
            preferences.adaptation_type if preferences is not None else AdaptationType.NONE,
        )
        if AdaptationType(current_adaptation) == AdaptationType.NONE:
            values["valid_until"] = None
        return await self.repository.upsert(profile_id, values, preferences)
