import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import AppUsage, DailyUsage
from app.schemas.usage import DailyUsageCreate


class UsageRepository:
    def __init__(self, db: AsyncSession): self.db = db

    async def create(self, profile_id: uuid.UUID, data: DailyUsageCreate) -> DailyUsage:
        values = data.model_dump(exclude={"applications"})
        usage = DailyUsage(profile_id=profile_id, **values)
        usage.applications = [AppUsage(**item.model_dump()) for item in data.applications]
        self.db.add(usage)
        await self.db.commit()
        return await self.get_by_date(profile_id, data.date)  # type: ignore[return-value]

    async def get_by_date(self, profile_id: uuid.UUID, day: date) -> DailyUsage | None:
        query = select(DailyUsage).options(selectinload(DailyUsage.applications)).where(DailyUsage.profile_id == profile_id, DailyUsage.date == day)
        return (await self.db.execute(query)).scalar_one_or_none()

    async def list(self, profile_id: uuid.UUID) -> list[DailyUsage]:
        query = select(DailyUsage).options(selectinload(DailyUsage.applications)).where(DailyUsage.profile_id == profile_id).order_by(DailyUsage.date.desc())
        return list((await self.db.execute(query)).scalars().all())

