import uuid

from app.repositories.profile_settings_repository import ProfileSettingsRepository
from app.schemas.profile_settings import ProfileSettingsRead, ProfileSettingsUpdate


class ProfileSettingsService:
    def __init__(self, repository: ProfileSettingsRepository):
        self.repository = repository

    async def get(self, profile_id: uuid.UUID) -> ProfileSettingsRead:
        settings = await self.repository.get(profile_id)
        if settings is None:
            return ProfileSettingsRead(profile_id=profile_id)
        return ProfileSettingsRead.model_validate(settings)

    async def upsert(self, profile_id: uuid.UUID, data: ProfileSettingsUpdate):
        return await self.repository.upsert(profile_id, data)
