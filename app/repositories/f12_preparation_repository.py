import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import F12EvidencePreparation, F12RawEvidenceCapture


class F12PreparationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def lock_evidence(
        self,
        evidence_id: uuid.UUID,
    ) -> F12RawEvidenceCapture | None:
        return await self.db.scalar(
            select(F12RawEvidenceCapture)
            .options(selectinload(F12RawEvidenceCapture.pairs))
            .where(F12RawEvidenceCapture.id == evidence_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def get_by_evidence_id(
        self,
        evidence_id: uuid.UUID,
    ) -> F12EvidencePreparation | None:
        return await self.db.scalar(
            select(F12EvidencePreparation).where(
                F12EvidencePreparation.evidence_id == evidence_id
            )
        )

    async def add(self, preparation: F12EvidencePreparation) -> None:
        self.db.add(preparation)
        await self.db.flush()

    async def commit(self) -> None:
        await self.db.commit()

    async def rollback(self) -> None:
        await self.db.rollback()
