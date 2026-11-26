from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from pydantic import ValidationError

from app.db.models import AdultMessageEvaluationJob
from app.schemas.adult_message import AdultMessageEvaluationRequest
from app.schemas.adult_message_window import AdultMessageWindowDiagnostic
from app.services.adult_message_evaluation_service import (
    AdultMessageEvaluationService,
    canonical_hash,
)
from app.services.adult_message_window_service import AdultMessageWindowService


class AdultMessageWeeklyProductionContractError(Exception):
    pass


@dataclass(frozen=True)
class AdultMessageWeeklyProductionResult:
    diagnostic: AdultMessageWindowDiagnostic
    job: AdultMessageEvaluationJob | None

    @property
    def blocked(self) -> bool:
        return self.job is None


class AdultMessageWeeklyProducer:
    """Freeze one verified real window into the existing durable transport."""

    def __init__(
        self,
        window_service: AdultMessageWindowService,
        evaluation_service: AdultMessageEvaluationService,
    ):
        self.window_service = window_service
        self.evaluation_service = evaluation_service

    async def produce(
        self,
        *,
        profile_id: uuid.UUID,
        evaluation_date: date,
    ) -> AdultMessageWeeklyProductionResult:
        diagnostic = await self.window_service.diagnose(
            profile_id=profile_id,
            evaluation_date=evaluation_date,
        )
        if not diagnostic.evaluation_ready:
            return AdultMessageWeeklyProductionResult(diagnostic=diagnostic, job=None)

        traceability = self._traceability(diagnostic)
        window_fingerprint = canonical_hash(traceability)
        request = self._request(
            diagnostic,
            request_id=f"adult-msg-real-{window_fingerprint}",
        )
        job = await self.evaluation_service.create_from_verified_window(
            request,
            window_fingerprint=window_fingerprint,
            traceability=traceability,
        )
        return AdultMessageWeeklyProductionResult(diagnostic=diagnostic, job=job)

    @staticmethod
    def _request(
        diagnostic: AdultMessageWindowDiagnostic,
        *,
        request_id: str,
    ) -> AdultMessageEvaluationRequest:
        selected = diagnostic.selected_series
        declarations = diagnostic.declarations
        if (
            selected is None
            or declarations.assignment.contract_value is not True
            or declarations.shared_phone.contract_value is not False
            or declarations.family_context.contract_value is None
            or declarations.consent.contract_value is not True
        ):
            raise AdultMessageWeeklyProductionContractError(
                "ready diagnostic lacks mandatory historical declarations"
            )
        body = {
            "request_id": request_id,
            "profile_id": str(diagnostic.profile_id),
            "series_id": selected.series_id,
            "time_zone": selected.time_zone,
            "evaluation_date": diagnostic.evaluation_date,
            "assignment_verified": declarations.assignment.contract_value,
            "shared_phone": declarations.shared_phone.contract_value,
            "context_declared": declarations.family_context.contract_value,
            "days": [
                {
                    "date": day.date,
                    "revision": day.revision,
                    "minutes": day.minutes,
                    "observed": day.f12_status
                    in {"OBSERVED", "OBSERVED_ZERO"},
                    "quality_verified": day.quality_verified,
                }
                for day in diagnostic.days
            ],
            "c1_evidence_ids": sorted(
                {
                    day.c1_evaluation_id
                    for day in diagnostic.days
                    if day.c1_evaluation_id is not None
                }
            ),
        }
        try:
            return AdultMessageEvaluationRequest.model_validate(body)
        except ValidationError as exc:
            raise AdultMessageWeeklyProductionContractError(
                "verified diagnostic does not satisfy AdultMessageEvaluationRequest"
            ) from exc

    @staticmethod
    def _traceability(diagnostic: AdultMessageWindowDiagnostic) -> dict:
        selected = diagnostic.selected_series
        if selected is None:
            raise AdultMessageWeeklyProductionContractError(
                "verified diagnostic has no unique series"
            )
        days = []
        for day in diagnostic.days:
            if (
                day.evidence_id is None
                or day.capture_id is None
                or day.evidence_content_sha256 is None
                or day.preparation_id is None
                or day.revision is None
                or not day.quality_verified
                or day.quality_policy_version is None
                or day.quality_audit_sha256 is None
                or day.minutes is None
            ):
                raise AdultMessageWeeklyProductionContractError(
                    "verified diagnostic lacks auditable daily evidence"
                )
            days.append(
                {
                    "date": day.date.isoformat(),
                    "revision": day.revision,
                    "evidence_id": str(day.evidence_id),
                    "capture_id": day.capture_id,
                    "evidence_content_sha256": day.evidence_content_sha256,
                    "preparation_id": str(day.preparation_id),
                    "quality_policy_version": day.quality_policy_version,
                    "quality_audit_sha256": day.quality_audit_sha256,
                    "c1_evaluation_id": day.c1_evaluation_id,
                }
            )

        def declaration(value):
            return {
                "contract_value": value.contract_value,
                "revisions": value.revisions,
                "record_ids": [str(item) for item in value.record_ids],
                "covered_dates": [item.isoformat() for item in value.covered_dates],
            }

        return {
            "schema_version": 1,
            "source": "REAL_VERIFIED",
            "profile_id": str(diagnostic.profile_id),
            "series_id": selected.series_id,
            "device_id": str(selected.device_id),
            "time_zone": selected.time_zone,
            "measurement_version": selected.measurement_version,
            "evaluation_date": diagnostic.evaluation_date.isoformat(),
            "window_start": diagnostic.window_start.isoformat(),
            "window_end": diagnostic.window_end.isoformat(),
            "days": days,
            "declarations": {
                "assignment": declaration(diagnostic.declarations.assignment),
                "shared_phone": declaration(diagnostic.declarations.shared_phone),
                "family_context": declaration(
                    diagnostic.declarations.family_context
                ),
                "consent": declaration(diagnostic.declarations.consent),
            },
        }
