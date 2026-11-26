import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AdultMessageEvaluationJob,
    AdultMessageEvaluationJobStatus,
    Profile,
)
from app.services.adult_messages_client import AdultMessageAnalyticsResult


class AdultMessageEvaluationIntegrityError(Exception):
    pass


class AdultMessageEvaluationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, job_id: uuid.UUID) -> AdultMessageEvaluationJob | None:
        return await self.db.get(AdultMessageEvaluationJob, job_id)

    async def get_by_request_id(
        self, request_id: str
    ) -> AdultMessageEvaluationJob | None:
        return await self.db.scalar(
            select(AdultMessageEvaluationJob).where(
                AdultMessageEvaluationJob.request_id == request_id
            )
        )

    async def get_by_fixture_id(
        self, fixture_id: str
    ) -> AdultMessageEvaluationJob | None:
        return await self.db.scalar(
            select(AdultMessageEvaluationJob).where(
                AdultMessageEvaluationJob.fixture_id == fixture_id
            )
        )

    async def get_by_window_fingerprint(
        self, window_fingerprint: str
    ) -> AdultMessageEvaluationJob | None:
        return await self.db.scalar(
            select(AdultMessageEvaluationJob).where(
                AdultMessageEvaluationJob.window_fingerprint == window_fingerprint
            )
        )

    async def lock_profile(self, profile_id: uuid.UUID) -> Profile | None:
        return await self.db.scalar(
            select(Profile)
            .where(Profile.id == profile_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def lock_job(
        self, job_id: uuid.UUID
    ) -> AdultMessageEvaluationJob | None:
        return await self.db.scalar(
            select(AdultMessageEvaluationJob)
            .where(AdultMessageEvaluationJob.id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def unresolved_for_profile(
        self,
        profile_id: uuid.UUID,
        *,
        excluding_request_id: str,
    ) -> AdultMessageEvaluationJob | None:
        return await self.db.scalar(
            select(AdultMessageEvaluationJob)
            .where(
                AdultMessageEvaluationJob.profile_id == profile_id,
                AdultMessageEvaluationJob.request_id != excluding_request_id,
                (
                    AdultMessageEvaluationJob.status.in_(
                        (
                            AdultMessageEvaluationJobStatus.PENDING,
                            AdultMessageEvaluationJobStatus.SENDING,
                        )
                    )
                    | (
                        (AdultMessageEvaluationJob.status
                         == AdultMessageEvaluationJobStatus.TECHNICAL_ERROR)
                        & AdultMessageEvaluationJob.retryable.is_(True)
                    )
                ),
            )
            .order_by(AdultMessageEvaluationJob.created_at.asc())
            .limit(1)
        )

    async def processed_for_series(
        self,
        profile_id: uuid.UUID,
        series_id: str,
    ) -> list[AdultMessageEvaluationJob]:
        return list(
            (
                await self.db.scalars(
                    select(AdultMessageEvaluationJob)
                    .where(
                        AdultMessageEvaluationJob.profile_id == profile_id,
                        AdultMessageEvaluationJob.series_id == series_id,
                        AdultMessageEvaluationJob.status
                        == AdultMessageEvaluationJobStatus.PROCESSED,
                    )
                    .order_by(
                        AdultMessageEvaluationJob.evaluation_date.asc(),
                        AdultMessageEvaluationJob.created_at.asc(),
                    )
                )
            ).all()
        )

    async def create(
        self, job: AdultMessageEvaluationJob
    ) -> AdultMessageEvaluationJob:
        self.db.add(job)
        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise AdultMessageEvaluationIntegrityError from exc
        await self.db.refresh(job)
        return job

    async def mark_sending(
        self, job: AdultMessageEvaluationJob
    ) -> AdultMessageEvaluationJob:
        job.status = AdultMessageEvaluationJobStatus.SENDING
        job.attempts += 1
        job.last_attempt_at = datetime.now(UTC)
        job.analytics_response = None
        job.analytics_evaluation_id = None
        job.analytics_card_id = None
        job.analytics_card_version = None
        job.analytics_replayed = None
        job.retryable = False
        job.last_error_code = None
        job.last_error_message = None
        await self.db.commit()
        await self.db.refresh(job)
        return job

    async def record_result(
        self,
        job: AdultMessageEvaluationJob,
        result: AdultMessageAnalyticsResult,
    ) -> AdultMessageEvaluationJob:
        job.analytics_response = result.response
        job.analytics_evaluation_id = result.evaluation_id
        job.analytics_card_id = result.card_id
        job.analytics_card_version = result.card_version
        job.analytics_replayed = result.replayed
        job.retryable = result.retryable
        job.last_error_code = result.error_code
        job.last_error_message = result.error_message
        if result.kind == "processed":
            job.status = AdultMessageEvaluationJobStatus.PROCESSED
        elif result.kind == "conflict":
            job.status = AdultMessageEvaluationJobStatus.CONFLICT
        else:
            job.status = AdultMessageEvaluationJobStatus.TECHNICAL_ERROR
        try:
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise
        await self.db.refresh(job)
        return job
