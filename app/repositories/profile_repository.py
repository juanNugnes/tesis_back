import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Profile, ProfileSettings
from app.schemas.profile import ProfileCreate, ProfileUpdate


class ProfileRepository:
    def __init__(self, db: AsyncSession): self.db = db

    async def create(self, data: ProfileCreate) -> Profile:
        values = data.model_dump(exclude={"settings"})
        profile = Profile(**values)
        if data.settings is not None:
            profile.settings = ProfileSettings(**data.settings.model_dump())
        self.db.add(profile)
        await self.db.commit()
        return await self.get(profile.id)

    async def get(self, profile_id: uuid.UUID) -> Profile | None:
        result = await self.db.execute(
            select(Profile).options(selectinload(Profile.settings)).where(Profile.id == profile_id)
        )
        return result.scalar_one_or_none()

    async def update(self, profile: Profile, data: ProfileUpdate) -> Profile:
        for key, value in data.model_dump(exclude_unset=True).items(): setattr(profile, key, value)
        await self.db.commit(); await self.db.refresh(profile)
        return profile
