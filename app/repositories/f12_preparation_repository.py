import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    Device,
    F12EvidencePreparation,
    F12RawEvidenceCapture,
    HistoricalSubmission,
    Profile,
)


class F12PreparationLinkConflictError(Exception):
    pass


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

    async def lock_by_id(
        self,
        preparation_id: uuid.UUID,
    ) -> F12EvidencePreparation | None:
        return await self.db.scalar(
            select(F12EvidencePreparation)
            .options(
                selectinload(F12EvidencePreparation.evidence).selectinload(
                    F12RawEvidenceCapture.pairs
                ),
                selectinload(F12EvidencePreparation.historical_submission),
            )
            .where(F12EvidencePreparation.id == preparation_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def lock_device(self, device_id: uuid.UUID) -> Device | None:
        return await self.db.scalar(
            select(Device)
            .options(selectinload(Device.profile).selectinload(Profile.settings))
            .where(Device.id == device_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def link_submission(
        self,
        *,
        preparation_id: uuid.UUID,
        submission: HistoricalSubmission,
        linked_at: datetime,
    ) -> F12EvidencePreparation:
        preparation = await self.lock_by_id(preparation_id)
        if preparation is None:
            raise F12PreparationLinkConflictError
        if preparation.historical_submission_id is not None:
            if preparation.historical_submission_id != submission.id:
                raise F12PreparationLinkConflictError
            await self.db.commit()
            return preparation
        preparation.historical_submission_id = submission.id
        preparation.submission_linked_at = linked_at
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            preparation = await self.lock_by_id(preparation_id)
            if (
                preparation is not None
                and preparation.historical_submission_id == submission.id
            ):
                return preparation
            raise F12PreparationLinkConflictError from None
        linked = await self.lock_by_id(preparation_id)
        if linked is None:
            raise F12PreparationLinkConflictError
        return linked

    async def add(self, preparation: F12EvidencePreparation) -> None:
        self.db.add(preparation)
        await self.db.flush()

    async def commit(self) -> None:
        await self.db.commit()

    async def rollback(self) -> None:
        await self.db.rollback()
