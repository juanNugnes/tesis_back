import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdultAccount


class AdultAccountRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, adult_id: uuid.UUID) -> AdultAccount | None:
        return await self.db.get(AdultAccount, adult_id)

    async def get_by_email(self, email: str) -> AdultAccount | None:
        result = await self.db.execute(select(AdultAccount).where(AdultAccount.email == email))
        return result.scalar_one_or_none()

    async def create(self, email: str) -> AdultAccount:
        adult = AdultAccount(email=email)
        self.db.add(adult)
        await self.db.flush()
        return adult
