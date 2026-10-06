import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DeviceBootstrapToken


class DeviceBootstrapTokenRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def token_hash_exists(self, token_hash: str) -> bool:
        result = await self.db.execute(
            select(DeviceBootstrapToken.id)
            .where(DeviceBootstrapToken.token_hash == token_hash)
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def create(
        self,
        profile_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
    ) -> DeviceBootstrapToken:
        bootstrap = DeviceBootstrapToken(
            profile_id=profile_id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        self.db.add(bootstrap)
        await self.db.flush()
        return bootstrap

    async def get_for_registration(self, token_hash: str) -> DeviceBootstrapToken | None:
        result = await self.db.execute(
            select(DeviceBootstrapToken)
            .where(DeviceBootstrapToken.token_hash == token_hash)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def mark_used_if_valid(
        self,
        bootstrap_id: uuid.UUID,
        profile_id: uuid.UUID,
        used_at: datetime,
    ) -> bool:
        result = await self.db.execute(
            update(DeviceBootstrapToken)
            .where(
                DeviceBootstrapToken.id == bootstrap_id,
                DeviceBootstrapToken.profile_id == profile_id,
                DeviceBootstrapToken.used_at.is_(None),
                DeviceBootstrapToken.revoked_at.is_(None),
                DeviceBootstrapToken.expires_at > used_at,
            )
            .values(used_at=used_at)
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1
