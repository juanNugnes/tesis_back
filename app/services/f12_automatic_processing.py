from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import HistoricalSubmissionStatus
from app.repositories.device_repository import DeviceRepository
from app.repositories.f12_preparation_repository import F12PreparationRepository
from app.repositories.f12_processing_repository import F12ProcessingRepository
from app.repositories.historical_submission_repository import (
    HistoricalSubmissionRepository,
)
from app.services.f12_preparation_service import (
    F12PreparationAccessDeniedError,
    F12PreparationContractError,
    F12PreparationIneligibleError,
    F12PreparationNotFoundError,
    F12PreparationPersistenceError,
    F12PreparationService,
)
from app.services.f12_submission_orchestrator import (
    F12SubmissionAuthorizationError,
    F12SubmissionContractError,
    F12SubmissionLinkConflictError,
    F12SubmissionNotCreatedError,
    F12SubmissionPersistenceError,
    F12SubmissionPreparationNotFoundError,
    F12SubmissionOrchestrator,
)
from app.services.historical_analytics_client import HistoricalAnalyticsClient
from app.services.historical_submission_service import (
    HistoricalSubmissionContentConflictError,
    HistoricalSubmissionNotFoundError,
    HistoricalSubmissionOrderError,
    HistoricalSubmissionRetryError,
    HistoricalSubmissionService,
)
from app.services.monitoring_authorization_service import (
    MonitoringAuthorizationService,
)


logger = logging.getLogger(__name__)


class F12AutomaticProcessingService:
    def __init__(
        self,
        db: AsyncSession,
        analytics_client: HistoricalAnalyticsClient,
        *,
        retry_seconds: int,
        max_attempts: int,
    ):
        self.db = db
        self.jobs = F12ProcessingRepository(db)
        self.retry_seconds = retry_seconds
        self.max_attempts = max_attempts
        historical = HistoricalSubmissionService(
            HistoricalSubmissionRepository(db), analytics_client
        )
        self.orchestrator = F12SubmissionOrchestrator(
            F12PreparationRepository(db),
            historical,
            MonitoringAuthorizationService(db),
        )

    async def process(self, job_id: uuid.UUID, *, worker_id: str) -> None:
        job = await self.jobs.get(job_id)
        if job is None or job.lease_owner != worker_id:
            return
        evidence = job.evidence
        if evidence is None:
            await self.jobs.mark_manual_review(
                job_id,
                worker_id=worker_id,
                code="evidence_not_found",
                message="The processing job no longer references stored evidence",
            )
            return

        try:
            self._validate_closed_day(evidence)
            F12PreparationService.validate_evidence_eligible(evidence)
            device = await DeviceRepository(self.db).get(evidence.device_id)
            authorized = await MonitoringAuthorizationService(
                self.db
            ).is_usage_monitoring_allowed(evidence.profile_id)
            if (
                device is None
                or device.profile_id != evidence.profile_id
                or not device.active
                or device.profile is None
                or not device.profile.active
                or not authorized
            ):
                raise F12SubmissionAuthorizationError

            preparation = await F12PreparationService(
                F12PreparationRepository(self.db)
            ).prepare(
                evidence_id=evidence.id,
                profile_id=evidence.profile_id,
                device_id=evidence.device_id,
            )
            submission = await HistoricalSubmissionRepository(
                self.db
            ).get_by_request_id(preparation.request_id)
            if submission is None:
                submission = await self.orchestrator.submit(
                    preparation_id=preparation.id
                )
            elif submission.status == HistoricalSubmissionStatus.PENDING:
                submission = await self.orchestrator.resume_pending(
                    preparation_id=preparation.id
                )
            elif submission.status == HistoricalSubmissionStatus.SENDING:
                await self.jobs.mark_manual_review(
                    job_id,
                    worker_id=worker_id,
                    code="submission_result_uncertain",
                    message=(
                        "Historical submission is SENDING; reconcile its request_id "
                        "before any explicit retry"
                    ),
                )
                return
            elif (
                submission.status == HistoricalSubmissionStatus.TECHNICAL_ERROR
                and submission.retryable
            ):
                submission = await self.orchestrator.retry(
                    preparation_id=preparation.id
                )
            elif submission.status == HistoricalSubmissionStatus.PROCESSED:
                # Reconcile a missing preparation link without another HTTP call.
                submission = await self.orchestrator.submit(
                    preparation_id=preparation.id
                )
            else:
                await self.jobs.mark_manual_review(
                    job_id,
                    worker_id=worker_id,
                    code="submission_not_automatically_retryable",
                    message=(
                        f"Historical submission is {submission.status.value}; "
                        "automatic retry is not allowed"
                    ),
                )
                return

            await self._record_submission_result(
                job_id,
                worker_id=worker_id,
                submission=submission,
            )
        except F12PreparationIneligibleError as exc:
            await self.jobs.mark_blocked(
                job_id,
                worker_id=worker_id,
                code=exc.code,
                message=str(exc),
            )
        except F12SubmissionAuthorizationError:
            await self.jobs.mark_blocked(
                job_id,
                worker_id=worker_id,
                code="monitoring_authorization_invalid",
                message="Device, profile, or monitoring authorization is not active",
            )
        except (F12PreparationContractError, F12SubmissionContractError) as exc:
            await self.jobs.mark_blocked(
                job_id,
                worker_id=worker_id,
                code="f12_contract_incompatible",
                message=str(exc),
            )
        except HistoricalSubmissionOrderError as exc:
            # A later day can be claimed while an earlier day of the same series is
            # still in flight in another process. Keep the frozen request retryable;
            # the existing series-order validation will run again after the delay.
            await self._technical_error(
                job_id,
                worker_id=worker_id,
                code="historical_series_order_pending",
                message=str(exc) or type(exc).__name__,
            )
        except (
            F12PreparationNotFoundError,
            F12PreparationAccessDeniedError,
            F12SubmissionPreparationNotFoundError,
            F12SubmissionLinkConflictError,
            F12SubmissionNotCreatedError,
            HistoricalSubmissionContentConflictError,
            HistoricalSubmissionNotFoundError,
            HistoricalSubmissionRetryError,
        ) as exc:
            await self.jobs.mark_manual_review(
                job_id,
                worker_id=worker_id,
                code="f12_manual_reconciliation_required",
                message=str(exc) or type(exc).__name__,
            )
        except (F12PreparationPersistenceError, F12SubmissionPersistenceError) as exc:
            await self._technical_error(
                job_id,
                worker_id=worker_id,
                code="f12_processing_persistence_error",
                message=str(exc) or type(exc).__name__,
            )
        except SQLAlchemyError as exc:
            await self.db.rollback()
            await self._technical_error(
                job_id,
                worker_id=worker_id,
                code="f12_processing_database_error",
                message=type(exc).__name__,
            )
        except Exception as exc:
            logger.exception("Unexpected automatic F12 processing failure")
            await self.db.rollback()
            await self._technical_error(
                job_id,
                worker_id=worker_id,
                code="f12_processing_unexpected_error",
                message=type(exc).__name__,
            )

    async def _record_submission_result(self, job_id, *, worker_id, submission):
        if submission.status == HistoricalSubmissionStatus.PROCESSED:
            await self.jobs.mark_completed(job_id, worker_id=worker_id)
        elif submission.status == HistoricalSubmissionStatus.TECHNICAL_ERROR:
            await self.jobs.mark_technical_error(
                job_id,
                worker_id=worker_id,
                code=submission.last_error_code or "historical_technical_error",
                message=submission.last_error_message or "Analytics evaluation failed",
                retryable=submission.retryable,
                retry_seconds=self.retry_seconds,
                max_attempts=self.max_attempts,
            )
        elif submission.status == HistoricalSubmissionStatus.SENDING:
            await self.jobs.mark_manual_review(
                job_id,
                worker_id=worker_id,
                code="submission_result_uncertain",
                message="Historical submission remained SENDING after transport",
            )
        else:
            await self.jobs.mark_manual_review(
                job_id,
                worker_id=worker_id,
                code="historical_submission_unresolved",
                message=f"Historical submission ended as {submission.status.value}",
            )

    async def _technical_error(self, job_id, *, worker_id, code, message):
        await self.jobs.mark_technical_error(
            job_id,
            worker_id=worker_id,
            code=code,
            message=message,
            retryable=True,
            retry_seconds=self.retry_seconds,
            max_attempts=self.max_attempts,
        )

    @staticmethod
    def _validate_closed_day(evidence) -> None:
        local_now = datetime.now(ZoneInfo(evidence.time_zone)).date()
        source_day = evidence.source_created_at.astimezone(
            ZoneInfo(evidence.time_zone)
        ).date()
        if evidence.local_date >= local_now or evidence.local_date >= source_day:
            raise F12PreparationIneligibleError(
                "local_day_not_closed",
                "Evidence local day is not closed",
            )


class F12AutomaticProcessingWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        analytics_service_url: str,
        analytics_service_token: str,
        analytics_timeout_seconds: float,
        poll_seconds: float,
        lease_seconds: int,
        retry_seconds: int,
        max_attempts: int,
    ):
        self.session_factory = session_factory
        self.analytics_service_url = analytics_service_url
        self.analytics_service_token = analytics_service_token
        self.analytics_timeout_seconds = analytics_timeout_seconds
        self.poll_seconds = poll_seconds
        self.lease_seconds = lease_seconds
        self.retry_seconds = retry_seconds
        self.max_attempts = max_attempts
        self.worker_id = str(uuid.uuid4())
        self.stop_event = asyncio.Event()

    async def run_once(self) -> bool:
        async with self.session_factory() as db:
            job = await F12ProcessingRepository(db).claim_next(
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
                max_attempts=self.max_attempts,
            )
            if job is None:
                return False
            client = HistoricalAnalyticsClient(
                self.analytics_service_url,
                self.analytics_service_token,
                self.analytics_timeout_seconds,
            )
            await F12AutomaticProcessingService(
                db,
                client,
                retry_seconds=self.retry_seconds,
                max_attempts=self.max_attempts,
            ).process(job.id, worker_id=self.worker_id)
            return True

    async def run_forever(self) -> None:
        while not self.stop_event.is_set():
            try:
                if await self.run_once():
                    continue
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Automatic F12 worker iteration failed")
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(), timeout=self.poll_seconds
                )
            except TimeoutError:
                pass

    def stop(self) -> None:
        self.stop_event.set()
