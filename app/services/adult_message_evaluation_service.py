import asyncio
import hashlib
import json
import threading
import uuid
import weakref
from datetime import date

from app.db.models import (
    AdultMessageEvaluationJob,
    AdultMessageEvaluationJobStatus,
)
from app.repositories.adult_message_evaluation_repository import (
    AdultMessageEvaluationIntegrityError,
    AdultMessageEvaluationRepository,
)
from app.schemas.adult_message import ControlledAdultMessageFixture
from app.services.adult_messages_client import AdultMessagesClient


class AdultMessageEvaluationNotFoundError(Exception):
    pass


class AdultMessageEvaluationProfileError(Exception):
    pass


class AdultMessageEvaluationContentConflictError(Exception):
    pass


class AdultMessageEvaluationPendingError(Exception):
    pass


class AdultMessageEvaluationRetryError(Exception):
    pass


class _JobLockRegistry:
    _guard = threading.Lock()
    _locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
        weakref.WeakValueDictionary()
    )

    @classmethod
    def get(cls, key: str) -> asyncio.Lock:
        with cls._guard:
            lock = cls._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                cls._locks[key] = lock
            return lock


def canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class AdultMessageEvaluationService:
    """Durable manual transport for explicitly fictitious controlled fixtures."""

    def __init__(
        self,
        repository: AdultMessageEvaluationRepository,
        client: AdultMessagesClient,
    ):
        self.repository = repository
        self.client = client

    async def create_from_fixture(
        self, fixture: ControlledAdultMessageFixture
    ) -> AdultMessageEvaluationJob:
        payload = fixture.payload.model_dump(mode="json")
        try:
            profile_id = uuid.UUID(payload["profile_id"])
        except (ValueError, AttributeError):
            raise AdultMessageEvaluationProfileError(
                "fixture profile_id must be a UUID"
            ) from None
        if str(profile_id) != payload["profile_id"]:
            raise AdultMessageEvaluationProfileError(
                "fixture profile_id must use canonical UUID form"
            )
        payload_sha256 = canonical_hash(payload)
        fixture_body = fixture.model_dump(mode="json")
        fixture_sha256 = canonical_hash(fixture_body)
        request_id = fixture.payload.request_id

        async with _JobLockRegistry.get(f"create:{profile_id}"):
            profile = await self.repository.lock_profile(profile_id)
            if profile is None or not profile.active:
                await self.repository.db.rollback()
                raise AdultMessageEvaluationProfileError(
                    "fixture profile does not exist or is inactive"
                )

            existing = await self.repository.get_by_request_id(request_id)
            fixture_existing = await self.repository.get_by_fixture_id(
                fixture.fixture_id
            )
            if existing is not None or fixture_existing is not None:
                candidate = existing or fixture_existing
                if (
                    existing is None
                    or fixture_existing is None
                    or existing.id != fixture_existing.id
                    or candidate.profile_id != profile_id
                    or candidate.fixture_id != fixture.fixture_id
                    or candidate.fixture_sha256 != fixture_sha256
                    or candidate.payload_sha256 != payload_sha256
                    or candidate.payload != payload
                ):
                    await self.repository.db.rollback()
                    raise AdultMessageEvaluationContentConflictError
                candidate_id = candidate.id
                await self.repository.db.rollback()
                restored = await self.repository.get(candidate_id)
                if restored is None:
                    raise AdultMessageEvaluationNotFoundError
                return restored

            unresolved = await self.repository.unresolved_for_profile(
                profile_id,
                excluding_request_id=request_id,
            )
            if unresolved is not None:
                await self.repository.db.rollback()
                raise AdultMessageEvaluationPendingError(
                    "another evaluation for this profile must be reconciled first"
                )

            processed = await self.repository.processed_for_series(
                profile_id, fixture.payload.series_id
            )
            try:
                self._validate_order_and_revisions(payload, processed)
            except AdultMessageEvaluationContentConflictError:
                await self.repository.db.rollback()
                raise
            job = AdultMessageEvaluationJob(
                fixture_id=fixture.fixture_id,
                fixture_sha256=fixture_sha256,
                request_id=request_id,
                profile_id=profile_id,
                series_id=fixture.payload.series_id,
                evaluation_date=fixture.payload.evaluation_date,
                payload=payload,
                payload_sha256=payload_sha256,
                status=AdultMessageEvaluationJobStatus.PENDING,
            )
            try:
                return await self.repository.create(job)
            except AdultMessageEvaluationIntegrityError:
                existing = await self.repository.get_by_request_id(request_id)
                if (
                    existing is not None
                    and existing.profile_id == profile_id
                    and existing.fixture_id == fixture.fixture_id
                    and existing.fixture_sha256 == fixture_sha256
                    and existing.payload_sha256 == payload_sha256
                    and existing.payload == payload
                ):
                    return existing
                raise AdultMessageEvaluationContentConflictError from None

    async def execute(
        self,
        job_id: uuid.UUID,
        *,
        retry: bool = False,
    ) -> AdultMessageEvaluationJob:
        current = await self.repository.get(job_id)
        if current is None:
            raise AdultMessageEvaluationNotFoundError
        async with _JobLockRegistry.get(f"execute:{current.request_id}"):
            profile = await self.repository.lock_profile(current.profile_id)
            if profile is None or not profile.active:
                await self.repository.db.rollback()
                raise AdultMessageEvaluationProfileError(
                    "fixture profile does not exist or is inactive"
                )
            job = await self.repository.lock_job(job_id)
            if job is None:
                await self.repository.db.rollback()
                raise AdultMessageEvaluationNotFoundError
            if job.profile_id != profile.id:
                await self.repository.db.rollback()
                raise AdultMessageEvaluationContentConflictError
            if job.status == AdultMessageEvaluationJobStatus.PROCESSED:
                job_id = job.id
                await self.repository.db.rollback()
                restored = await self.repository.get(job_id)
                if restored is None:
                    raise AdultMessageEvaluationNotFoundError
                return restored
            if job.status == AdultMessageEvaluationJobStatus.CONFLICT:
                await self.repository.db.rollback()
                raise AdultMessageEvaluationRetryError(
                    "conflicting evaluations cannot be retried with a new identity"
                )
            if job.status == AdultMessageEvaluationJobStatus.PENDING:
                pass
            elif job.status == AdultMessageEvaluationJobStatus.SENDING:
                if not retry:
                    await self.repository.db.rollback()
                    raise AdultMessageEvaluationPendingError(
                        "the previous result is uncertain; explicit recovery is required"
                    )
            elif job.status == AdultMessageEvaluationJobStatus.TECHNICAL_ERROR:
                if not retry or not job.retryable:
                    await self.repository.db.rollback()
                    raise AdultMessageEvaluationRetryError
            else:
                await self.repository.db.rollback()
                raise AdultMessageEvaluationRetryError

            job = await self.repository.mark_sending(job)
            result = await self.client.evaluate(job.payload)
            return await self.repository.record_result(job, result)

    async def create_and_execute(
        self, fixture: ControlledAdultMessageFixture
    ) -> AdultMessageEvaluationJob:
        job = await self.create_from_fixture(fixture)
        return await self.execute(job.id)

    @staticmethod
    def _validate_order_and_revisions(
        payload: dict,
        processed: list[AdultMessageEvaluationJob],
    ) -> None:
        if not processed:
            return
        if any(job.payload.get("time_zone") != payload["time_zone"] for job in processed):
            raise AdultMessageEvaluationContentConflictError(
                "one series cannot change time_zone"
            )
        latest_evaluation = max(job.evaluation_date for job in processed)
        evaluation_date = payload["evaluation_date"]
        if isinstance(evaluation_date, str):
            evaluation_date = date.fromisoformat(evaluation_date)
        if evaluation_date < latest_evaluation:
            raise AdultMessageEvaluationContentConflictError(
                "evaluations cannot move backwards"
            )

        latest_days: dict[str, dict] = {}
        for job in processed:
            for row in job.payload["days"]:
                previous = latest_days.get(row["date"])
                if previous is None or row["revision"] > previous["revision"]:
                    latest_days[row["date"]] = row

        increased = False
        for row in payload["days"]:
            previous = latest_days.get(row["date"])
            if previous is None:
                continue
            if row["revision"] < previous["revision"]:
                raise AdultMessageEvaluationContentConflictError(
                    "daily revisions cannot decrease"
                )
            if row["revision"] == previous["revision"] and row != previous:
                raise AdultMessageEvaluationContentConflictError(
                    "changed daily content requires a greater revision"
                )
            if row["revision"] > previous["revision"]:
                increased = True
        if evaluation_date == latest_evaluation and not increased:
            raise AdultMessageEvaluationContentConflictError(
                "a correction for the same Monday requires a greater daily revision"
            )
