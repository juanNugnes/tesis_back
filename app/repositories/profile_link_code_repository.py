import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProfileLinkCode


class ProfileLinkCodeRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def code_hash_exists(self, code_hash: str) -> bool:
        result = await self.db.execute(
            select(ProfileLinkCode.id).where(ProfileLinkCode.code_hash == code_hash).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def create(
        self,
        profile_id: uuid.UUID,
        code_hash: str,
        purpose: str,
        expires_at: datetime,
    ) -> ProfileLinkCode:
        link_code = ProfileLinkCode(
            profile_id=profile_id,
            code_hash=code_hash,
            purpose=purpose,
            expires_at=expires_at,
        )
        self.db.add(link_code)
        await self.db.flush()
        return link_code

    async def get_for_claim(self, code_hash: str) -> ProfileLinkCode | None:
        result = await self.db.execute(
            select(ProfileLinkCode)
            .where(ProfileLinkCode.code_hash == code_hash)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def mark_used_if_valid(
        self,
        link_code_id: uuid.UUID,
        purpose: str,
        used_at: datetime,
    ) -> bool:
        result = await self.db.execute(
            update(ProfileLinkCode)
            .where(
                ProfileLinkCode.id == link_code_id,
                ProfileLinkCode.purpose == purpose,
                ProfileLinkCode.used_at.is_(None),
                ProfileLinkCode.revoked_at.is_(None),
                ProfileLinkCode.expires_at > used_at,
            )
            .values(used_at=used_at)
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1

    async def revoke_pending_for_profile(
        self,
        profile_id: uuid.UUID,
        purpose: str,
        revoked_at: datetime,
    ) -> int:
        result = await self.db.execute(
            update(ProfileLinkCode)
            .where(
                ProfileLinkCode.profile_id == profile_id,
                ProfileLinkCode.purpose == purpose,
                ProfileLinkCode.used_at.is_(None),
                ProfileLinkCode.revoked_at.is_(None),
                ProfileLinkCode.expires_at > revoked_at,
            )
            .values(revoked_at=revoked_at)
            .execution_options(synchronize_session=False)
        )
        return result.rowcount
