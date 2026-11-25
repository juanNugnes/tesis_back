from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    F12EvidencePreparation,
    F12ProcessingJob,
    F12ProcessingJobStatus,
    F12RawEvidenceCapture,
)


class F12ProcessingRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, job_id: uuid.UUID) -> F12ProcessingJob | None:
        return await self.db.scalar(
            select(F12ProcessingJob)
            .options(
                selectinload(F12ProcessingJob.evidence)
                .selectinload(F12RawEvidenceCapture.pairs),
                selectinload(F12ProcessingJob.evidence)
                .selectinload(F12RawEvidenceCapture.preparation)
                .selectinload(F12EvidencePreparation.historical_submission),
            )
            .where(F12ProcessingJob.id == job_id)
            .execution_options(populate_existing=True)
        )

    async def get_by_evidence_id(
        self, evidence_id: uuid.UUID
    ) -> F12ProcessingJob | None:
        return await self.db.scalar(
            select(F12ProcessingJob).where(
                F12ProcessingJob.evidence_id == evidence_id
            )
        )

    async def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        max_attempts: int,
        now: datetime | None = None,
    ) -> F12ProcessingJob | None:
        now = (now or datetime.now(UTC)).astimezone(UTC)
        claimable = or_(
            F12ProcessingJob.status == F12ProcessingJobStatus.PENDING,
            and_(
                F12ProcessingJob.status
                == F12ProcessingJobStatus.TECHNICAL_ERROR,
                F12ProcessingJob.retryable.is_(True),
                F12ProcessingJob.attempts < max_attempts,
                or_(
                    F12ProcessingJob.next_attempt_at.is_(None),
                    F12ProcessingJob.next_attempt_at <= now,
                ),
            ),
            and_(
                F12ProcessingJob.status == F12ProcessingJobStatus.PROCESSING,
                F12ProcessingJob.lease_expires_at.is_not(None),
                F12ProcessingJob.lease_expires_at <= now,
                F12ProcessingJob.attempts < max_attempts,
            ),
        )
        candidate_ids = list(
            (
                await self.db.scalars(
                    select(F12ProcessingJob.id)
                    .where(claimable)
                    .order_by(
                        F12ProcessingJob.created_at.asc(),
                        F12ProcessingJob.id.asc(),
                    )
                    .limit(10)
                )
            ).all()
        )
        for candidate_id in candidate_ids:
            claimed_id = await self.db.scalar(
                update(F12ProcessingJob)
                .where(F12ProcessingJob.id == candidate_id, claimable)
                .values(
                    status=F12ProcessingJobStatus.PROCESSING,
                    attempts=F12ProcessingJob.attempts + 1,
                    retryable=False,
                    next_attempt_at=None,
                    lease_owner=worker_id,
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    updated_at=now,
                )
                .returning(F12ProcessingJob.id)
                .execution_options(synchronize_session=False)
            )
            if claimed_id is not None:
                await self.db.commit()
                return await self.get(claimed_id)
            await self.db.rollback()
        await self.db.rollback()
        return None

    async def mark_completed(
        self, job_id: uuid.UUID, *, worker_id: str
    ) -> F12ProcessingJob:
        return await self._finish(
            job_id,
            worker_id=worker_id,
            status=F12ProcessingJobStatus.COMPLETED,
            completed_at=datetime.now(UTC),
        )

    async def mark_blocked(
        self,
        job_id: uuid.UUID,
        *,
        worker_id: str,
        code: str,
        message: str,
    ) -> F12ProcessingJob:
        return await self._finish(
            job_id,
            worker_id=worker_id,
            status=F12ProcessingJobStatus.BLOCKED,
            code=code,
            message=message,
        )

    async def mark_manual_review(
        self,
        job_id: uuid.UUID,
        *,
        worker_id: str,
        code: str,
        message: str,
    ) -> F12ProcessingJob:
        return await self._finish(
            job_id,
            worker_id=worker_id,
            status=F12ProcessingJobStatus.MANUAL_REVIEW,
            code=code,
            message=message,
        )

    async def mark_technical_error(
        self,
        job_id: uuid.UUID,
        *,
        worker_id: str,
        code: str,
        message: str,
        retryable: bool,
        retry_seconds: int,
        max_attempts: int,
    ) -> F12ProcessingJob:
        job = await self.get(job_id)
        if job is None or job.lease_owner != worker_id:
            raise RuntimeError("F12 processing lease was lost")
        can_retry = retryable and job.attempts < max_attempts
        now = datetime.now(UTC)
        job.status = F12ProcessingJobStatus.TECHNICAL_ERROR
        job.retryable = can_retry
        job.next_attempt_at = (
            now + timedelta(seconds=retry_seconds) if can_retry else None
        )
        job.lease_owner = None
        job.lease_expires_at = None
        job.last_error_code = code[:100]
        job.last_error_message = message[:500]
        job.updated_at = now
        await self.db.commit()
        await self.db.refresh(job)
        return job

    async def _finish(
        self,
        job_id: uuid.UUID,
        *,
        worker_id: str,
        status: F12ProcessingJobStatus,
        code: str | None = None,
        message: str | None = None,
        completed_at: datetime | None = None,
    ) -> F12ProcessingJob:
        job = await self.get(job_id)
        if job is None or job.lease_owner != worker_id:
            raise RuntimeError("F12 processing lease was lost")
        job.status = status
        job.retryable = False
        job.next_attempt_at = None
        job.lease_owner = None
        job.lease_expires_at = None
        job.last_error_code = code[:100] if code else None
        job.last_error_message = message[:500] if message else None
        job.completed_at = completed_at
        job.updated_at = datetime.now(UTC)
        await self.db.commit()
        await self.db.refresh(job)
        return job
