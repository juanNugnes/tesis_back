from __future__ import annotations

import asyncio
import re
import uuid
from datetime import UTC, datetime
from importlib import metadata

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.db.models import F12EvidencePreparation, F12RawEvidenceCapture
from app.repositories.f12_preparation_repository import F12PreparationRepository
from app.schemas.historical_submission import (
    HISTORICAL_BRIDGE_VERSION,
    HISTORICAL_EXPORT_ADAPTER_VERSION,
    HISTORICAL_MEASUREMENT_VERSION,
    HistoricalSubmissionCreate,
)
from app.services.f12_published_runner import (
    PUBLISHED_PREPARER_COMMIT,
    F12PublishedPreparationError,
    run_published_preparer,
)
from app.services.historical_submission_service import HistoricalSubmissionService


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class F12PreparationNotFoundError(Exception):
    pass


class F12PreparationAccessDeniedError(Exception):
    pass


class F12PreparationIneligibleError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class F12PreparationContractError(Exception):
    pass


class F12PreparationPersistenceError(Exception):
    pass


class F12PreparationService:
    """Prepare one stored evidence revision without invoking Analytics C1."""

    def __init__(self, repository: F12PreparationRepository):
        self.repository = repository

    async def prepare(
        self,
        *,
        evidence_id: uuid.UUID,
        profile_id: uuid.UUID,
        device_id: uuid.UUID,
    ) -> F12EvidencePreparation:
        try:
            evidence = await self.repository.lock_evidence(evidence_id)
            if evidence is None:
                raise F12PreparationNotFoundError
            if evidence.profile_id != profile_id or evidence.device_id != device_id:
                raise F12PreparationAccessDeniedError

            existing = await self.repository.get_by_evidence_id(evidence_id)
            if existing is not None:
                self._validate_existing(existing, evidence)
                await self.repository.commit()
                return existing

            self._validate_eligible(evidence)
            request_id = f"f12prep-{evidence.capture_id}"
            binding = {
                "schema_version": 1,
                "request_id": request_id,
                "series": {
                    "profile_id": str(evidence.profile_id),
                    "device_id": str(evidence.device_id),
                    "assignment_id": str(evidence.device_id),
                    "time_zone": evidence.time_zone,
                    "measurement_version": HISTORICAL_MEASUREMENT_VERSION,
                },
                "local_date": evidence.local_date.isoformat(),
                "computed_at": _as_utc(evidence.source_created_at).isoformat(),
            }
            pairs = tuple(
                (pair.summary_bytes, pair.events_bytes)
                for pair in sorted(evidence.pairs, key=lambda item: item.ordinal)
            )
            try:
                result = await asyncio.to_thread(
                    run_published_preparer,
                    binding,
                    pairs,
                )
            except F12PublishedPreparationError as exc:
                raise F12PreparationContractError(str(exc)) from exc
            except OSError as exc:
                raise F12PreparationPersistenceError(
                    "Temporary preparation storage is unavailable"
                ) from exc

            data = self._validate_result(
                result,
                evidence=evidence,
                request_id=request_id,
            )
            audit = result["audit"]
            prepared_identity = HistoricalSubmissionService._prepare_submission(
                profile_id=evidence.profile_id,
                device_id=evidence.device_id,
                data=data,
            )
            if (
                data.representation.subject_id != prepared_identity.series_key
                or data.representation.representation_record_id
                != prepared_identity.representation_record_id
            ):
                raise F12PreparationContractError(
                    "Published and backend historical identities do not match"
                )

            preparation = F12EvidencePreparation(
                evidence_id=evidence.id,
                request_id=request_id,
                preparer_commit=PUBLISHED_PREPARER_COMMIT,
                measurement_version=HISTORICAL_MEASUREMENT_VERSION,
                bridge_version=HISTORICAL_BRIDGE_VERSION,
                export_adapter_version=HISTORICAL_EXPORT_ADAPTER_VERSION,
                tzdata_version=metadata.version("tzdata"),
                series_key=data.representation.subject_id,
                representation_record_id=data.representation.representation_record_id,
                input_fingerprint=data.representation.provenance.input_fingerprint,
                projection_fingerprint=audit["projection_fingerprint"],
                evidence_content_sha256=evidence.content_sha256,
                submission_body=data.model_dump(mode="json"),
                audit=audit,
            )
            await self.repository.add(preparation)
            await self.repository.commit()
            return preparation
        except (
            F12PreparationNotFoundError,
            F12PreparationAccessDeniedError,
            F12PreparationIneligibleError,
            F12PreparationContractError,
            F12PreparationPersistenceError,
        ):
            await self.repository.rollback()
            raise
        except IntegrityError:
            await self.repository.rollback()
            existing = await self.repository.get_by_evidence_id(evidence_id)
            if existing is not None:
                evidence = await self.repository.lock_evidence(evidence_id)
                if evidence is not None:
                    self._validate_existing(existing, evidence)
                    await self.repository.commit()
                    return existing
            raise F12PreparationPersistenceError from None
        except SQLAlchemyError as exc:
            await self.repository.rollback()
            raise F12PreparationPersistenceError from exc

    @staticmethod
    def _validate_eligible(evidence: F12RawEvidenceCapture) -> None:
        if (
            evidence.expires_at is not None
            and _as_utc(evidence.expires_at) <= datetime.now(UTC)
        ):
            raise F12PreparationIneligibleError(
                "evidence_expired",
                "Evidence retention expired before preparation",
            )
        if evidence.evidence_state == "UNAVAILABLE":
            raise F12PreparationIneligibleError(
                "unavailable_policy_undefined",
                "UNAVAILABLE evidence is retained but is not prepared as zero or missing",
            )
        if evidence.revision != 1:
            raise F12PreparationIneligibleError(
                "corrected_revision_policy_undefined",
                "Corrected evidence revisions are not prepared until replacement policy is defined",
            )
        if evidence.receipt_status != "RECEIVED" or not evidence.pairs:
            raise F12PreparationIneligibleError(
                "evidence_not_ready",
                "Evidence is not available for preparation",
            )

    @staticmethod
    def _validate_result(
        result: dict,
        *,
        evidence: F12RawEvidenceCapture,
        request_id: str,
    ) -> HistoricalSubmissionCreate:
        request = result.get("request")
        audit = result.get("audit")
        if not isinstance(request, dict) or not isinstance(audit, dict):
            raise F12PreparationContractError("Published preparer result is incomplete")
        body = {
            "request_id": request.get("request_id"),
            "time_zone": evidence.time_zone,
            "measurement_version": HISTORICAL_MEASUREMENT_VERSION,
            "representation": request.get("representation"),
        }
        try:
            data = HistoricalSubmissionCreate.model_validate(body)
        except ValidationError as exc:
            raise F12PreparationContractError(
                "Prepared representation does not satisfy HistoricalSubmissionCreate"
            ) from exc
        if data.request_id != request_id:
            raise F12PreparationContractError("Published preparer changed request_id")
        provenance = data.representation.provenance
        if provenance.source_version != HISTORICAL_EXPORT_ADAPTER_VERSION:
            raise F12PreparationContractError("Unexpected F12 export provenance")
        if (
            audit.get("audit_schema") != "f12-export-lineage-v1"
            or audit.get("export_adapter_version")
            != HISTORICAL_EXPORT_ADAPTER_VERSION
            or audit.get("projection_bridge_version") != HISTORICAL_BRIDGE_VERSION
            or audit.get("export_evidence_state") != "PAIRS_AVAILABLE"
            or not _SHA256_PATTERN.fullmatch(
                str(audit.get("projection_fingerprint", ""))
            )
            or not _SHA256_PATTERN.fullmatch(provenance.input_fingerprint)
        ):
            raise F12PreparationContractError("Unexpected F12 preparation audit")

        expected_originals = sorted(
            (
                {
                    "collector_run_id": pair.collector_run_id,
                    "event_count": None,
                    "summary_sha256": pair.summary_sha256,
                    "events_sha256": pair.events_sha256,
                }
                for pair in evidence.pairs
            ),
            key=lambda item: item["collector_run_id"],
        )
        originals = audit.get("originals")
        if not isinstance(originals, list) or len(originals) != len(expected_originals):
            raise F12PreparationContractError("F12 audit does not match stored originals")
        for actual, expected in zip(originals, expected_originals, strict=True):
            if not isinstance(actual, dict) or any(
                actual.get(key) != expected[key]
                for key in ("collector_run_id", "summary_sha256", "events_sha256")
            ):
                raise F12PreparationContractError("F12 audit does not match stored originals")
        return data

    @staticmethod
    def _validate_existing(
        preparation: F12EvidencePreparation,
        evidence: F12RawEvidenceCapture,
    ) -> None:
        if (
            preparation.evidence_content_sha256 != evidence.content_sha256
            or preparation.preparer_commit != PUBLISHED_PREPARER_COMMIT
            or preparation.measurement_version != HISTORICAL_MEASUREMENT_VERSION
            or preparation.bridge_version != HISTORICAL_BRIDGE_VERSION
            or preparation.export_adapter_version != HISTORICAL_EXPORT_ADAPTER_VERSION
            or preparation.tzdata_version != metadata.version("tzdata")
        ):
            raise F12PreparationContractError(
                "Stored preparation was created with a different immutable binding"
            )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
