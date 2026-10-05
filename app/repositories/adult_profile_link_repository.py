import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import AdultProfileLink, Profile


class AdultProfileLinkRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_link(self, adult_id: uuid.UUID, profile_id: uuid.UUID) -> AdultProfileLink | None:
        result = await self.db.execute(
            select(AdultProfileLink).where(
                AdultProfileLink.adult_id == adult_id,
                AdultProfileLink.profile_id == profile_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_link_for_update(
        self,
        adult_id: uuid.UUID,
        profile_id: uuid.UUID,
    ) -> AdultProfileLink | None:
        result = await self.db.execute(
            select(AdultProfileLink)
            .where(
                AdultProfileLink.adult_id == adult_id,
                AdultProfileLink.profile_id == profile_id,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_active_link(self, adult_id: uuid.UUID, profile_id: uuid.UUID) -> AdultProfileLink | None:
        result = await self.db.execute(
            select(AdultProfileLink).where(
                AdultProfileLink.adult_id == adult_id,
                AdultProfileLink.profile_id == profile_id,
                AdultProfileLink.active.is_(True),
                AdultProfileLink.revoked_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def create_link(self, adult_id: uuid.UUID, profile_id: uuid.UUID) -> AdultProfileLink:
        link = AdultProfileLink(adult_id=adult_id, profile_id=profile_id)
        self.db.add(link)
        await self.db.flush()
        return link

    async def revoke_link(self, link: AdultProfileLink, revoked_at: datetime) -> AdultProfileLink:
        link.active = False
        link.revoked_at = revoked_at
        await self.db.flush()
        return link

    async def list_profiles_for_adult(self, adult_id: uuid.UUID) -> list[Profile]:
        result = await self.db.execute(
            select(Profile)
            .join(AdultProfileLink, AdultProfileLink.profile_id == Profile.id)
            .options(selectinload(Profile.settings))
            .where(
                AdultProfileLink.adult_id == adult_id,
                AdultProfileLink.active.is_(True),
                AdultProfileLink.revoked_at.is_(None),
            )
            .order_by(Profile.created_at.asc())
        )
        return list(result.scalars().all())
