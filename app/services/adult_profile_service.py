import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdultAccount, AdultProfileLink, Profile
from app.repositories.adult_profile_link_repository import AdultProfileLinkRepository
from app.repositories.profile_repository import ProfileRepository
from app.repositories.representative_verification_repository import RepresentativeVerificationRepository


class AdultProfileService:
    def __init__(
        self,
        db: AsyncSession,
        repository: AdultProfileLinkRepository | None = None,
    ):
        self.db = db
        self.repository = repository or AdultProfileLinkRepository(db)
        self.profiles = ProfileRepository(db)
        self.verifications = RepresentativeVerificationRepository(db)

    async def get_authorized_profile(
        self,
        adult: AdultAccount,
        profile_id: uuid.UUID,
    ) -> Profile | None:
        link = await self.repository.get_active_link(adult.id, profile_id)
        if link is None:
            return None
        return await self.profiles.get(profile_id)

    async def create_link(self, adult: AdultAccount, profile: Profile) -> AdultProfileLink:
        link = await self.repository.get_link(adult.id, profile.id)
        if link is None:
            link = await self.repository.create_link(adult.id, profile.id)
        else:
            link.active = True
            link.revoked_at = None
        verification = await self.verifications.get_by_adult_profile(adult.id, profile.id)
        if verification is None:
            await self.verifications.create_pending(adult.id, profile.id)
        await self.db.commit()
        return link

    async def revoke_link(self, adult: AdultAccount, profile_id: uuid.UUID) -> bool:
        link = await self.repository.get_active_link(adult.id, profile_id)
        if link is None:
            await self.db.rollback()
            return False
        await self.repository.revoke_link(link, datetime.now(timezone.utc))
        await self.db.commit()
        return True

    async def list_profiles(self, adult: AdultAccount) -> list[Profile]:
        return await self.repository.list_profiles_for_adult(adult.id)
