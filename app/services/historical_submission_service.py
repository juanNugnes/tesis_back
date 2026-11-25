import asyncio
import hashlib
import json
import threading
import uuid
import weakref
from datetime import UTC, date, datetime, time, timedelta
from importlib import metadata
from zoneinfo import ZoneInfo

from app.db.models import (
    Device,
    HistoricalSubmission,
    HistoricalSubmissionStatus,
)
from app.repositories.historical_submission_repository import (
    HistoricalSubmissionIntegrityError,
    HistoricalSubmissionRepository,
)
from app.schemas.historical_submission import (
    HISTORICAL_BRIDGE_VERSION,
    HISTORICAL_REPRESENTATION_SPEC_ID,
    HistoricalSubmissionCreate,
)
from app.services.historical_analytics_client import HistoricalAnalyticsClient


class HistoricalSubmissionNotFoundError(Exception):
    pass


class HistoricalSubmissionAccessDeniedError(Exception):
    pass


class HistoricalSubmissionContentConflictError(Exception):
    pass


class HistoricalSubmissionOrderError(Exception):
    pass


class HistoricalSubmissionRetryError(Exception):
    pass


class _SeriesLockRegistry:
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


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _midnight(day: date, zone: ZoneInfo) -> datetime:
    naive = datetime.combine(day, time.min)
    candidates = set()
    for fold in (0, 1):
        instant = naive.replace(tzinfo=zone, fold=fold).astimezone(UTC)
        if instant.astimezone(zone).replace(tzinfo=None) == naive:
            candidates.add(instant)
    if len(candidates) != 1:
        raise ValueError(
            "local midnight is ambiguous or nonexistent and requires an explicit policy"
        )
    return candidates.pop()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class HistoricalSubmissionService:
    def __init__(
        self,
        repository: HistoricalSubmissionRepository,
        client: HistoricalAnalyticsClient,
    ):
        self.repository = repository
        self.client = client

    async def create(
        self,
        *,
        profile_id: uuid.UUID,
        device: Device,
        data: HistoricalSubmissionCreate,
    ) -> HistoricalSubmission:
        prepared = self._prepare_submission(
            profile_id=profile_id,
            device_id=device.id,
            data=data,
        )
        async with _SeriesLockRegistry.get(prepared.series_key):
            await self.repository.lock_device(device.id)
            existing = await self.repository.get_by_request_id(data.request_id)
            if existing is not None:
                self._validate_idempotent_create(existing, prepared)
                existing_id = existing.id
                await self.repository.db.rollback()
                existing = await self.repository.get(existing_id)
                if existing is None:
                    raise HistoricalSubmissionNotFoundError
                return existing

            if (
                await self.repository.get_by_series_date(
                    prepared.series_key,
                    prepared.local_date,
                )
                is not None
                or await self.repository.get_by_representation_record_id(
                    prepared.representation_record_id
                )
                is not None
            ):
                await self.repository.db.rollback()
                raise HistoricalSubmissionContentConflictError

            latest = await self.repository.latest_for_series(prepared.series_key)
            if latest is not None:
                if prepared.interval_start < _as_utc(latest.interval_end):
                    await self.repository.db.rollback()
                    raise HistoricalSubmissionOrderError
                if latest.status != HistoricalSubmissionStatus.PROCESSED:
                    await self.repository.db.rollback()
                    raise HistoricalSubmissionOrderError

            try:
                submission = await self.repository.create(prepared)
            except HistoricalSubmissionIntegrityError:
                existing = await self.repository.get_by_request_id(data.request_id)
                if existing is not None:
                    self._validate_idempotent_create(existing, prepared)
                    return existing
                raise HistoricalSubmissionContentConflictError from None
            return await self._send_locked(
                submission.id,
                allowed_status=HistoricalSubmissionStatus.PENDING,
            )

    async def retry(
        self,
        *,
        submission_id: uuid.UUID,
        profile_id: uuid.UUID,
        device_id: uuid.UUID,
    ) -> HistoricalSubmission:
        submission = await self.repository.get(submission_id)
        if submission is None:
            raise HistoricalSubmissionNotFoundError
        self._validate_access(submission, profile_id=profile_id, device_id=device_id)
        async with _SeriesLockRegistry.get(submission.series_key):
            await self.repository.lock_device(device_id)
            locked = await self.repository.lock_submission(submission_id)
            if locked is None:
                await self.repository.db.rollback()
                raise HistoricalSubmissionNotFoundError
            self._validate_access(locked, profile_id=profile_id, device_id=device_id)
            if (
                locked.status != HistoricalSubmissionStatus.TECHNICAL_ERROR
                or not locked.retryable
            ):
                await self.repository.db.rollback()
                raise HistoricalSubmissionRetryError
            return await self._send_locked(
                submission_id,
                allowed_status=HistoricalSubmissionStatus.TECHNICAL_ERROR,
                already_locked=locked,
            )

    async def resume_pending(
        self,
        *,
        submission_id: uuid.UUID,
        profile_id: uuid.UUID,
        device_id: uuid.UUID,
    ) -> HistoricalSubmission:
        """Resume a row committed before transport; never accepts SENDING."""
        submission = await self.repository.get(submission_id)
        if submission is None:
            raise HistoricalSubmissionNotFoundError
        self._validate_access(submission, profile_id=profile_id, device_id=device_id)
        async with _SeriesLockRegistry.get(submission.series_key):
            await self.repository.lock_device(device_id)
            locked = await self.repository.lock_submission(submission_id)
            if locked is None:
                await self.repository.db.rollback()
                raise HistoricalSubmissionNotFoundError
            self._validate_access(locked, profile_id=profile_id, device_id=device_id)
            if locked.status != HistoricalSubmissionStatus.PENDING:
                await self.repository.db.rollback()
                raise HistoricalSubmissionRetryError
            return await self._send_locked(
                submission_id,
                allowed_status=HistoricalSubmissionStatus.PENDING,
                already_locked=locked,
            )

    async def _send_locked(
        self,
        submission_id: uuid.UUID,
        *,
        allowed_status: HistoricalSubmissionStatus,
        already_locked: HistoricalSubmission | None = None,
    ) -> HistoricalSubmission:
        submission = already_locked
        if submission is None:
            submission = await self.repository.lock_submission(submission_id)
        if submission is None:
            await self.repository.db.rollback()
            raise HistoricalSubmissionNotFoundError
        if submission.status != allowed_status:
            await self.repository.db.rollback()
            raise HistoricalSubmissionRetryError
        preceding = await self.repository.unresolved_preceding(submission)
        if preceding is not None:
            await self.repository.db.rollback()
            raise HistoricalSubmissionOrderError

        submission = await self.repository.mark_sending(submission)
        result = await self.client.evaluate(submission.payload)
        return await self.repository.record_result(submission, result)

    @staticmethod
    def _validate_access(
        submission: HistoricalSubmission,
        *,
        profile_id: uuid.UUID,
        device_id: uuid.UUID,
    ) -> None:
        if submission.profile_id != profile_id or submission.device_id != device_id:
            raise HistoricalSubmissionAccessDeniedError

    @staticmethod
    def _validate_idempotent_create(
        existing: HistoricalSubmission,
        prepared: HistoricalSubmission,
    ) -> None:
        if (
            existing.profile_id != prepared.profile_id
            or existing.device_id != prepared.device_id
            or existing.assignment_id != prepared.assignment_id
            or existing.payload_sha256 != prepared.payload_sha256
        ):
            raise HistoricalSubmissionContentConflictError

    @staticmethod
    def _prepare_submission(
        *,
        profile_id: uuid.UUID,
        device_id: uuid.UUID,
        data: HistoricalSubmissionCreate,
    ) -> HistoricalSubmission:
        zone = ZoneInfo(data.time_zone)
        supplied_start = data.representation.interval_start.astimezone(UTC)
        supplied_end = data.representation.interval_end.astimezone(UTC)
        local_date = supplied_start.astimezone(zone).date()
        interval_start = _midnight(local_date, zone)
        interval_end = _midnight(local_date + timedelta(days=1), zone)
        if supplied_start != interval_start or supplied_end != interval_end:
            raise ValueError(
                "representation interval must match the complete local day in time_zone"
            )

        tzdata_version = metadata.version("tzdata")
        assignment_id = device_id
        binding = {
            "profile_id": str(profile_id),
            "device_id": str(device_id),
            "assignment_id": str(assignment_id),
            "time_zone": data.time_zone,
            "measurement_version": data.measurement_version,
            "bridge_version": HISTORICAL_BRIDGE_VERSION,
            "tzdata_version": tzdata_version,
        }
        series_key = "series-" + _canonical_hash(binding)
        representation_record_id = "rep-" + _canonical_hash(
            {
                "subject_id": series_key,
                "begin": interval_start.isoformat(),
                "end": interval_end.isoformat(),
                "spec": "v2",
            }
        )
        representation = data.representation.model_dump(mode="json")
        representation.update(
            {
                "representation_record_id": representation_record_id,
                "representation_spec_id": HISTORICAL_REPRESENTATION_SPEC_ID,
                "subject_id": series_key,
                "interval_start": interval_start.isoformat(),
                "interval_end": interval_end.isoformat(),
                "computed_at": data.representation.computed_at.astimezone(UTC).isoformat(),
            }
        )
        payload = {
            "request_id": data.request_id,
            "representation": representation,
        }
        return HistoricalSubmission(
            request_id=data.request_id,
            profile_id=profile_id,
            device_id=device_id,
            assignment_id=assignment_id,
            local_date=local_date,
            time_zone=data.time_zone,
            measurement_version=data.measurement_version,
            bridge_version=HISTORICAL_BRIDGE_VERSION,
            tzdata_version=tzdata_version,
            series_key=series_key,
            representation_record_id=representation_record_id,
            interval_start=interval_start,
            interval_end=interval_end,
            payload=payload,
            payload_sha256=_canonical_hash(payload),
            status=HistoricalSubmissionStatus.PENDING,
        )
