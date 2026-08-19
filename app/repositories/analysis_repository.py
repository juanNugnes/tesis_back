import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AnalysisResult


class AnalysisRepository:
    def __init__(self, db: AsyncSession): self.db = db

    async def create(self, result: AnalysisResult) -> AnalysisResult:
        self.db.add(result); await self.db.commit(); await self.db.refresh(result)
        return result

    async def list(self, profile_id: uuid.UUID) -> list[AnalysisResult]:
        query = select(AnalysisResult).where(AnalysisResult.profile_id == profile_id).order_by(AnalysisResult.analyzed_at.desc())
        return list((await self.db.execute(query)).scalars().all())

    async def latest(self, profile_id: uuid.UUID) -> AnalysisResult | None:
        query = select(AnalysisResult).where(AnalysisResult.profile_id == profile_id).order_by(AnalysisResult.analyzed_at.desc()).limit(1)
        return (await self.db.execute(query)).scalar_one_or_none()

