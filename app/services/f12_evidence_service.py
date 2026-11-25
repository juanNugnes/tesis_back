from __future__ import annotations

import asyncio
import threading
import uuid
import weakref
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.db.models import (
    F12ProcessingJob,
    F12ProcessingJobStatus,
    F12RawEvidenceCapture,
    F12RawEvidencePair,
)
from app.repositories.f12_evidence_repository import F12EvidenceRepository
from app.schemas.f12_evidence import F12EvidenceReceipt
from app.services.f12_evidence_validator import (
    F12EvidenceValidationError,
    ValidatedF12Evidence,
)


class F12EvidenceConflictError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class F12EvidencePersistenceError(Exception):
    pass


@dataclass(frozen=True)
class F12EvidenceIngestResult:
    receipt: F12EvidenceReceipt
    created: bool


class _EvidenceLockRegistry:
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


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class F12EvidenceService:
    def __init__(
        self,
        repository: F12EvidenceRepository,
        *,
        retention_days: int | None,
    ):
        self.repository = repository
        self.retention_days = retention_days

    async def ingest(
        self,
        evidence: ValidatedF12Evidence,
        *,
        profile_id: uuid.UUID,
        device_id: uuid.UUID,
    ) -> F12EvidenceIngestResult:
        manifest = evidence.manifest
        key = f"{profile_id}:{device_id}:{manifest.local_date.isoformat()}"
        async with _EvidenceLockRegistry.get(key):
            return await self._ingest_locked(
                evidence,
                profile_id=profile_id,
                device_id=device_id,
            )

    async def _ingest_locked(
        self,
        evidence: ValidatedF12Evidence,
        *,
        profile_id: uuid.UUID,
        device_id: uuid.UUID,
    ) -> F12EvidenceIngestResult:
        manifest = evidence.manifest
        now = datetime.now(UTC)
        try:
            device = await self.repository.lock_device(device_id)
            if device is None or device.profile_id != profile_id:
                raise F12EvidenceConflictError(
                    "evidence_owner_conflict",
                    "Evidence owner does not match the authenticated device",
                )

            await self.repository.purge_expired(profile_id=profile_id, now=now)
            existing = await self.repository.get_by_capture_id(manifest.capture_id)
            if manifest.capture_id != evidence.expected_capture_id:
                if existing is not None:
                    raise F12EvidenceConflictError(
                        "capture_id_conflict",
                        "capture_id was already used with different evidence",
                    )
                raise F12EvidenceValidationError(
                    "capture_id_mismatch",
                    "capture_id does not match the authenticated evidence binding",
                )
            if existing is not None:
                if not self._matches(
                    existing,
                    evidence,
                    profile_id=profile_id,
                    device_id=device_id,
                ):
                    raise F12EvidenceConflictError(
                        "capture_id_conflict",
                        "capture_id was already used with different evidence",
                    )
                await self.repository.commit()
                return F12EvidenceIngestResult(self._receipt(existing), created=False)

            same_revision = await self.repository.get_by_revision(
                profile_id=profile_id,
                device_id=device_id,
                local_date=manifest.local_date,
                revision=manifest.revision,
            )
            if same_revision is not None:
                raise F12EvidenceConflictError(
                    "evidence_revision_conflict",
                    "The evidence revision already exists with different content",
                )
            latest_revision = await self.repository.latest_revision(
                profile_id=profile_id,
                device_id=device_id,
                local_date=manifest.local_date,
            )
            if latest_revision is not None and manifest.revision <= latest_revision:
                raise F12EvidenceConflictError(
                    "stale_evidence_revision",
                    "A correction must use a revision greater than the latest stored revision",
                )

            expires_at = (
                now + timedelta(days=self.retention_days)
                if self.retention_days is not None
                else None
            )
            capture = F12RawEvidenceCapture(
                capture_id=manifest.capture_id,
                profile_id=profile_id,
                device_id=device_id,
                local_date=manifest.local_date,
                time_zone=manifest.time_zone,
                revision=manifest.revision,
                envelope_version=manifest.envelope_version,
                adapter_version=manifest.adapter_version,
                evidence_state=manifest.evidence_state,
                unavailable_reason=manifest.unavailable_reason,
                content_sha256=manifest.content_sha256,
                pair_count=len(evidence.pairs),
                total_bytes=evidence.total_bytes,
                source_created_at=evidence.source_created_at,
                receipt_status="RECEIVED",
                received_at=now,
                expires_at=expires_at,
            )
            capture.pairs = [
                F12RawEvidencePair(
                    ordinal=ordinal,
                    collector_run_id=pair.collector_run_id,
                    summary_sha256=pair.summary_sha256,
                    events_sha256=pair.events_sha256,
                    summary_size=len(pair.summary_bytes),
                    events_size=len(pair.events_bytes),
                    summary_bytes=pair.summary_bytes,
                    events_bytes=pair.events_bytes,
                )
                for ordinal, pair in enumerate(evidence.pairs)
            ]
            if manifest.evidence_state == "AVAILABLE" and manifest.revision == 1:
                capture.processing_job = F12ProcessingJob(
                    status=F12ProcessingJobStatus.PENDING,
                )
            else:
                code = (
                    "unavailable_policy_undefined"
                    if manifest.evidence_state == "UNAVAILABLE"
                    else "corrected_revision_policy_undefined"
                )
                capture.processing_job = F12ProcessingJob(
                    status=F12ProcessingJobStatus.BLOCKED,
                    retryable=False,
                    last_error_code=code,
                    last_error_message=(
                        "UNAVAILABLE evidence is retained but is not processed"
                        if manifest.evidence_state == "UNAVAILABLE"
                        else "Corrected evidence revisions are retained but are not processed"
                    ),
                )
            await self.repository.add(capture)
            await self.repository.commit()
            return F12EvidenceIngestResult(self._receipt(capture), created=True)
        except (F12EvidenceConflictError, F12EvidenceValidationError):
            await self.repository.rollback()
            raise
        except IntegrityError:
            await self.repository.rollback()
            try:
                return await self._resolve_integrity_race(
                    evidence,
                    profile_id=profile_id,
                    device_id=device_id,
                )
            except (F12EvidenceConflictError, F12EvidencePersistenceError):
                raise
            except SQLAlchemyError as exc:
                raise F12EvidencePersistenceError from exc
        except SQLAlchemyError as exc:
            await self.repository.rollback()
            raise F12EvidencePersistenceError from exc

    async def _resolve_integrity_race(
        self,
        evidence: ValidatedF12Evidence,
        *,
        profile_id: uuid.UUID,
        device_id: uuid.UUID,
    ) -> F12EvidenceIngestResult:
        existing = await self.repository.get_by_capture_id(evidence.manifest.capture_id)
        if existing is not None and self._matches(
            existing,
            evidence,
            profile_id=profile_id,
            device_id=device_id,
        ):
            return F12EvidenceIngestResult(self._receipt(existing), created=False)
        same_revision = await self.repository.get_by_revision(
            profile_id=profile_id,
            device_id=device_id,
            local_date=evidence.manifest.local_date,
            revision=evidence.manifest.revision,
        )
        if same_revision is not None:
            raise F12EvidenceConflictError(
                "evidence_revision_conflict",
                "The evidence revision already exists with different content",
            )
        raise F12EvidencePersistenceError

    @staticmethod
    def _matches(
        stored: F12RawEvidenceCapture,
        evidence: ValidatedF12Evidence,
        *,
        profile_id: uuid.UUID,
        device_id: uuid.UUID,
    ) -> bool:
        manifest = evidence.manifest
        if (
            stored.profile_id != profile_id
            or stored.device_id != device_id
            or stored.local_date != manifest.local_date
            or stored.time_zone != manifest.time_zone
            or stored.revision != manifest.revision
            or stored.envelope_version != manifest.envelope_version
            or stored.adapter_version != manifest.adapter_version
            or stored.evidence_state != manifest.evidence_state
            or stored.unavailable_reason != manifest.unavailable_reason
            or stored.content_sha256 != manifest.content_sha256
            or stored.pair_count != len(evidence.pairs)
            or stored.total_bytes != evidence.total_bytes
            or _as_utc(stored.source_created_at) != evidence.source_created_at
            or len(stored.pairs) != len(evidence.pairs)
        ):
            return False
        return all(
            persisted.ordinal == ordinal
            and persisted.collector_run_id == incoming.collector_run_id
            and persisted.summary_sha256 == incoming.summary_sha256
            and persisted.events_sha256 == incoming.events_sha256
            and persisted.summary_bytes == incoming.summary_bytes
            and persisted.events_bytes == incoming.events_bytes
            for ordinal, (persisted, incoming) in enumerate(
                zip(stored.pairs, evidence.pairs, strict=True)
            )
        )

    @staticmethod
    def _receipt(capture: F12RawEvidenceCapture) -> F12EvidenceReceipt:
        return F12EvidenceReceipt(
            receipt_id=capture.id,
            capture_id=capture.capture_id,
            status=capture.receipt_status,
            evidence_state=capture.evidence_state,
            local_date=capture.local_date,
            revision=capture.revision,
            content_sha256=capture.content_sha256,
            pair_count=capture.pair_count,
            total_bytes=capture.total_bytes,
            received_at=_as_utc(capture.received_at),
            expires_at=(
                _as_utc(capture.expires_at)
                if capture.expires_at is not None
                else None
            ),
        )
