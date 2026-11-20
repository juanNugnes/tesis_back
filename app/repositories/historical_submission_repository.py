import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Device,
    HistoricalSubmission,
    HistoricalSubmissionStatus,
)
from app.services.historical_analytics_client import HistoricalAnalyticsResult


class HistoricalSubmissionIntegrityError(Exception):
    pass


class HistoricalSubmissionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, submission_id: uuid.UUID) -> HistoricalSubmission | None:
        return await self.db.get(HistoricalSubmission, submission_id)

    async def list_for_profile(
        self,
        profile_id: uuid.UUID,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        submission_status: HistoricalSubmissionStatus | None = None,
    ) -> list[HistoricalSubmission]:
        query = select(HistoricalSubmission).where(
            HistoricalSubmission.profile_id == profile_id
        )
        if date_from is not None:
            query = query.where(HistoricalSubmission.local_date >= date_from)
        if date_to is not None:
            query = query.where(HistoricalSubmission.local_date <= date_to)
        if submission_status is not None:
            query = query.where(HistoricalSubmission.status == submission_status)
        query = query.order_by(
            HistoricalSubmission.local_date.desc(),
            HistoricalSubmission.created_at.desc(),
        )
        return list((await self.db.scalars(query)).all())

    async def get_by_request_id(self, request_id: str) -> HistoricalSubmission | None:
        return await self.db.scalar(
            select(HistoricalSubmission).where(
                HistoricalSubmission.request_id == request_id
            )
        )

    async def get_by_series_date(
        self,
        series_key: str,
        local_date: date,
    ) -> HistoricalSubmission | None:
        return await self.db.scalar(
            select(HistoricalSubmission).where(
                HistoricalSubmission.series_key == series_key,
                HistoricalSubmission.local_date == local_date,
            )
        )

    async def get_by_representation_record_id(
        self,
        representation_record_id: str,
    ) -> HistoricalSubmission | None:
        return await self.db.scalar(
            select(HistoricalSubmission).where(
                HistoricalSubmission.representation_record_id
                == representation_record_id
            )
        )

    async def lock_device(self, device_id: uuid.UUID) -> Device | None:
        return await self.db.scalar(
            select(Device)
            .where(Device.id == device_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def lock_submission(
        self,
        submission_id: uuid.UUID,
    ) -> HistoricalSubmission | None:
        return await self.db.scalar(
            select(HistoricalSubmission)
            .where(HistoricalSubmission.id == submission_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def latest_for_series(self, series_key: str) -> HistoricalSubmission | None:
        return await self.db.scalar(
            select(HistoricalSubmission)
            .where(HistoricalSubmission.series_key == series_key)
            .order_by(HistoricalSubmission.interval_end.desc())
            .limit(1)
        )

    async def unresolved_preceding(
        self,
        submission: HistoricalSubmission,
    ) -> HistoricalSubmission | None:
        return await self.db.scalar(
            select(HistoricalSubmission)
            .where(
                HistoricalSubmission.series_key == submission.series_key,
                HistoricalSubmission.interval_start < submission.interval_start,
                HistoricalSubmission.status != HistoricalSubmissionStatus.PROCESSED,
            )
            .order_by(HistoricalSubmission.interval_start.asc())
            .limit(1)
        )

    async def create(self, submission: HistoricalSubmission) -> HistoricalSubmission:
        self.db.add(submission)
        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise HistoricalSubmissionIntegrityError from exc
        await self.db.refresh(submission)
        return submission

    async def mark_sending(
        self,
        submission: HistoricalSubmission,
    ) -> HistoricalSubmission:
        submission.status = HistoricalSubmissionStatus.SENDING
        submission.attempts += 1
        submission.last_attempt_at = datetime.now(UTC)
        submission.analytics_response = None
        submission.analytics_evaluation_id = None
        submission.analytics_replayed = None
        submission.retryable = False
        submission.last_error_code = None
        submission.last_error_message = None
        await self.db.commit()
        await self.db.refresh(submission)
        return submission

    async def record_result(
        self,
        submission: HistoricalSubmission,
        result: HistoricalAnalyticsResult,
    ) -> HistoricalSubmission:
        submission.analytics_response = result.response
        submission.analytics_evaluation_id = result.evaluation_id
        submission.analytics_replayed = result.replayed
        submission.retryable = result.retryable
        submission.last_error_code = result.error_code
        submission.last_error_message = result.error_message
        if result.kind == "processed":
            submission.status = HistoricalSubmissionStatus.PROCESSED
        elif result.kind == "conflict":
            submission.status = HistoricalSubmissionStatus.CONFLICT
        else:
            submission.status = HistoricalSubmissionStatus.TECHNICAL_ERROR
        await self.db.commit()
        await self.db.refresh(submission)
        return submission
