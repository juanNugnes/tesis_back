import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdultOtpChallenge


class AdultOtpRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        challenge_id: uuid.UUID,
        adult_id: uuid.UUID,
        code_hash: str,
        expires_at: datetime,
    ) -> AdultOtpChallenge:
        challenge = AdultOtpChallenge(
            id=challenge_id,
            adult_id=adult_id,
            code_hash=code_hash,
            expires_at=expires_at,
        )
        self.db.add(challenge)
        await self.db.flush()
        return challenge

    async def get_latest_for_verification(self, adult_id: uuid.UUID) -> AdultOtpChallenge | None:
        result = await self.db.execute(
            select(AdultOtpChallenge)
            .where(AdultOtpChallenge.adult_id == adult_id)
            .order_by(AdultOtpChallenge.created_at.desc())
            .limit(1)
            .with_for_update()
        )
        return result.scalar_one_or_none()
