from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from importlib import metadata

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.db.models import (
    Device,
    HistoricalSubmission,
)
from app.repositories.f12_preparation_repository import (
    F12PreparationLinkConflictError,
    F12PreparationRepository,
)
from app.schemas.historical_submission import HistoricalSubmissionCreate
from app.services.f12_preparation_service import (
    F12PreparationContractError,
    F12PreparationIneligibleError,
    F12PreparationService,
)
from app.services.historical_submission_service import HistoricalSubmissionService
from app.services.monitoring_authorization_service import (
    MonitoringAuthorizationService,
)


F12_C1_TZDATA_VERSION = "2026.5"


class F12SubmissionPreparationNotFoundError(Exception):
    pass


class F12SubmissionAuthorizationError(Exception):
    pass


class F12SubmissionContractError(Exception):
    pass


class F12SubmissionLinkConflictError(Exception):
    pass


class F12SubmissionNotCreatedError(Exception):
    pass


class F12SubmissionPersistenceError(Exception):
    pass


@dataclass(frozen=True)
class _EligibleBinding:
    preparation_id: uuid.UUID
    request_id: str
    profile_id: uuid.UUID
    device_id: uuid.UUID
    local_date: date
    series_key: str
    representation_record_id: str
    linked_submission_id: uuid.UUID | None


class F12SubmissionOrchestrator:
    """Explicitly hand one persisted F12 preparation to the existing C1 flow."""

    def __init__(
        self,
        repository: F12PreparationRepository,
        historical_service: HistoricalSubmissionService,
        monitoring_authorization: MonitoringAuthorizationService,
    ):
        self.repository = repository
        self.historical_service = historical_service
        self.monitoring_authorization = monitoring_authorization

    async def submit(
        self,
        *,
        preparation_id: uuid.UUID,
    ) -> HistoricalSubmission:
        try:
            binding, device, data = await self._load_eligible(
                preparation_id
            )
            submission = await self.historical_service.create(
                profile_id=binding.profile_id,
                device=device,
                data=data,
            )
            self._validate_submission(submission, binding=binding)
            await self.repository.link_submission(
                preparation_id=binding.preparation_id,
                submission=submission,
                linked_at=datetime.now(UTC),
            )
            return submission
        except F12PreparationIneligibleError:
            await self.repository.rollback()
            raise
        except F12PreparationLinkConflictError:
            await self.repository.rollback()
            raise F12SubmissionLinkConflictError from None
        except (
            F12SubmissionPreparationNotFoundError,
            F12SubmissionAuthorizationError,
            F12SubmissionContractError,
            F12SubmissionLinkConflictError,
        ):
            await self.repository.rollback()
            raise
        except SQLAlchemyError as exc:
            await self.repository.rollback()
            raise F12SubmissionPersistenceError from exc

    async def retry(
        self,
        *,
        preparation_id: uuid.UUID,
    ) -> HistoricalSubmission:
        try:
            binding, device, data = await self._load_eligible(
                preparation_id
            )
            existing = await self.historical_service.repository.get_by_request_id(
                binding.request_id
            )
            if existing is None:
                raise F12SubmissionNotCreatedError
            existing_id = existing.id
            if (
                binding.linked_submission_id is not None
                and binding.linked_submission_id != existing_id
            ):
                raise F12SubmissionLinkConflictError

            # create() performs the authoritative idempotency/content check but
            # returns the existing row without sending it again.
            reconciled = await self.historical_service.create(
                profile_id=binding.profile_id,
                device=device,
                data=data,
            )
            if reconciled.id != existing_id:
                raise F12SubmissionLinkConflictError
            self._validate_submission(reconciled, binding=binding)
            await self.repository.link_submission(
                preparation_id=binding.preparation_id,
                submission=reconciled,
                linked_at=datetime.now(UTC),
            )
            return await self.historical_service.retry(
                submission_id=reconciled.id,
                profile_id=binding.profile_id,
                device_id=binding.device_id,
            )
        except F12PreparationIneligibleError:
            await self.repository.rollback()
            raise
        except F12PreparationLinkConflictError:
            await self.repository.rollback()
            raise F12SubmissionLinkConflictError from None
        except (
            F12SubmissionPreparationNotFoundError,
            F12SubmissionAuthorizationError,
            F12SubmissionContractError,
            F12SubmissionLinkConflictError,
            F12SubmissionNotCreatedError,
        ):
            await self.repository.rollback()
            raise
        except SQLAlchemyError as exc:
            await self.repository.rollback()
            raise F12SubmissionPersistenceError from exc

    async def _load_eligible(
        self,
        preparation_id: uuid.UUID,
    ) -> tuple[_EligibleBinding, Device, HistoricalSubmissionCreate]:
        preparation = await self.repository.lock_by_id(preparation_id)
        if preparation is None or preparation.evidence is None:
            raise F12SubmissionPreparationNotFoundError
        evidence = preparation.evidence
        try:
            F12PreparationService.validate_evidence_eligible(evidence)
            F12PreparationService.validate_persisted(preparation, evidence)
        except F12PreparationContractError as exc:
            raise F12SubmissionContractError(str(exc)) from exc

        if (
            metadata.version("tzdata") != F12_C1_TZDATA_VERSION
            or preparation.tzdata_version != F12_C1_TZDATA_VERSION
        ):
            raise F12SubmissionContractError(
                "F12/C1 series identity requires tzdata 2026.5"
            )

        device = await self.repository.lock_device(evidence.device_id)
        if (
            device is None
            or device.profile_id != evidence.profile_id
            or not device.active
            or device.profile is None
            or device.profile.id != evidence.profile_id
            or not device.profile.active
        ):
            raise F12SubmissionAuthorizationError
        if not await self.monitoring_authorization.is_usage_monitoring_allowed(
            evidence.profile_id
        ):
            raise F12SubmissionAuthorizationError

        try:
            data = HistoricalSubmissionCreate.model_validate(
                preparation.submission_body
            )
        except ValidationError as exc:
            raise F12SubmissionContractError(
                "Persisted submission_body is not a HistoricalSubmissionCreate"
            ) from exc
        if data.model_dump(mode="json") != preparation.submission_body:
            raise F12SubmissionContractError(
                "Persisted submission_body is not canonical"
            )
        representation = data.representation
        if (
            data.request_id != preparation.request_id
            or data.time_zone != evidence.time_zone
            or data.measurement_version != preparation.measurement_version
            or representation.subject_id != preparation.series_key
            or representation.representation_record_id
            != preparation.representation_record_id
            or representation.provenance.input_fingerprint
            != preparation.input_fingerprint
        ):
            raise F12SubmissionContractError(
                "Persisted preparation binding does not match submission_body"
            )

        prepared = HistoricalSubmissionService._prepare_submission(
            profile_id=evidence.profile_id,
            device_id=evidence.device_id,
            data=data,
        )
        normalized_body = {
            "request_id": prepared.payload["request_id"],
            "time_zone": preparation.submission_body["time_zone"],
            "measurement_version": preparation.submission_body[
                "measurement_version"
            ],
            "representation": prepared.payload["representation"],
        }
        try:
            normalized_data = HistoricalSubmissionCreate.model_validate(
                normalized_body
            )
        except ValidationError as exc:
            raise F12SubmissionContractError(
                "Historical submission normalization is not contract compatible"
            ) from exc
        if (
            prepared.local_date != evidence.local_date
            or prepared.series_key != preparation.series_key
            or prepared.representation_record_id
            != preparation.representation_record_id
            or normalized_data != data
        ):
            raise F12SubmissionContractError(
                "Historical submission would alter the prepared representation"
            )
        if (
            preparation.historical_submission is not None
            and preparation.historical_submission.request_id != preparation.request_id
        ):
            raise F12SubmissionLinkConflictError
        return (
            _EligibleBinding(
                preparation_id=preparation.id,
                request_id=preparation.request_id,
                profile_id=evidence.profile_id,
                device_id=evidence.device_id,
                local_date=evidence.local_date,
                series_key=preparation.series_key,
                representation_record_id=preparation.representation_record_id,
                linked_submission_id=preparation.historical_submission_id,
            ),
            device,
            data,
        )

    @staticmethod
    def _validate_submission(
        submission: HistoricalSubmission,
        *,
        binding: _EligibleBinding,
    ) -> None:
        if (
            submission.request_id != binding.request_id
            or submission.profile_id != binding.profile_id
            or submission.device_id != binding.device_id
            or submission.assignment_id != binding.device_id
            or submission.local_date != binding.local_date
            or submission.series_key != binding.series_key
            or submission.representation_record_id
            != binding.representation_record_id
        ):
            raise F12SubmissionLinkConflictError
