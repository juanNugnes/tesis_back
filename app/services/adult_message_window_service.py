from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from pydantic import ValidationError

from app.db.models import F12RawEvidenceCapture, HistoricalSubmissionStatus
from app.repositories.adult_message_window_repository import (
    AdultMessageWindowRepository,
)
from app.schemas.adult_message_window import (
    AdultMessageContextRecord,
    AdultMessageDeclarationDiagnostic,
    AdultMessageHistoricalDeclaration,
    AdultMessageSeriesCandidate,
    AdultMessageWindowBlocker,
    AdultMessageWindowDay,
    AdultMessageWindowDiagnostic,
)
from app.schemas.historical_submission import HistoricalSubmissionCreate
from app.services.f12_operational_quality import (
    F12_OPERATIONAL_QUALITY_POLICY_VERSION,
)


class AdultMessageWindowProfileNotFoundError(Exception):
    pass


class AdultMessageWindowDateError(Exception):
    pass


class AdultMessageWindowService:
    """Build a read-only diagnosis; never freezes or sends an evaluation."""

    def __init__(self, repository: AdultMessageWindowRepository):
        self.repository = repository

    async def diagnose(
        self,
        *,
        profile_id: uuid.UUID,
        evaluation_date: date,
    ) -> AdultMessageWindowDiagnostic:
        if evaluation_date.weekday() != 0:
            raise AdultMessageWindowDateError("evaluation_date must be a Monday")
        if not await self.repository.profile_exists(profile_id):
            raise AdultMessageWindowProfileNotFoundError

        dates = [evaluation_date - timedelta(days=offset) for offset in range(28, 0, -1)]
        window_start, window_end = dates[0], dates[-1]
        evidence = await self.repository.list_evidence(
            profile_id,
            date_from=window_start,
            date_to=window_end,
        )
        contexts = await self.repository.list_contexts(
            profile_id,
            date_from=window_start,
            date_to=window_end,
        )
        weekly_declarations = await self.repository.list_weekly_declarations(
            profile_id,
            date_from=window_start,
            date_to=window_end,
        )
        consents = await self.repository.list_usage_monitoring_consents(profile_id)
        candidates = self._series_candidates(evidence)
        selected = candidates[0] if len(candidates) == 1 else None

        by_date: dict[date, list[F12RawEvidenceCapture]] = defaultdict(list)
        for item in evidence:
            by_date[item.local_date].append(item)
        days = [
            self._day(
                day,
                period="REFERENCE" if index < 14 else "RECENT",
                all_evidence=by_date.get(day, []),
                selected=selected,
                series_problem=(
                    "SERIES_AMBIGUOUS"
                    if len(candidates) > 1
                    else "SERIES_NOT_FOUND"
                    if selected is None
                    else None
                ),
            )
            for index, day in enumerate(dates)
        ]

        current_device = await self.repository.current_device_is_linked(
            profile_id,
            selected.device_id if selected else None,
        )
        current_shared_phone = (
            await self.repository.has_current_shared_phone_setting(profile_id)
        )
        declarations = self._declarations(
            contexts,
            weekly_declarations,
            consents,
            selected=selected,
            dates=dates,
            current_device=current_device,
            current_shared_phone=current_shared_phone,
        )
        blockers = self._blockers(days, candidates, selected, declarations)
        observed = sum(
            item.f12_status in {"OBSERVED", "OBSERVED_ZERO"} for item in days
        )
        available = sum(item.evidence_available for item in days)
        return AdultMessageWindowDiagnostic(
            profile_id=profile_id,
            evaluation_date=evaluation_date,
            window_start=window_start,
            window_end=window_end,
            reference_start=dates[0],
            reference_end=dates[13],
            recent_start=dates[14],
            recent_end=dates[27],
            selected_series=selected,
            candidate_series=candidates,
            days=days,
            declarations=declarations,
            available_day_count=available,
            observed_day_count=observed,
            missing_day_count=28 - observed,
            evaluation_ready=not blockers,
            blockers=blockers,
        )

    @staticmethod
    def _series_candidates(
        evidence: list[F12RawEvidenceCapture],
    ) -> list[AdultMessageSeriesCandidate]:
        grouped: dict[tuple[str, uuid.UUID, str, str], set[date]] = defaultdict(set)
        for item in evidence:
            preparation = item.preparation
            if preparation is None:
                continue
            grouped[
                (
                    preparation.series_key,
                    item.device_id,
                    item.time_zone,
                    preparation.measurement_version,
                )
            ].add(item.local_date)
        candidates = [
            AdultMessageSeriesCandidate(
                series_id=key[0],
                device_id=key[1],
                time_zone=key[2],
                measurement_version=key[3],
                prepared_day_count=len(days),
                first_date=min(days),
                last_date=max(days),
            )
            for key, days in grouped.items()
        ]
        return sorted(
            candidates,
            key=lambda item: (
                str(item.device_id),
                item.time_zone,
                item.measurement_version,
                item.series_id,
            ),
        )

    @classmethod
    def _day(
        cls,
        day: date,
        *,
        period: str,
        all_evidence: list[F12RawEvidenceCapture],
        selected: AdultMessageSeriesCandidate | None,
        series_problem: str | None,
    ) -> AdultMessageWindowDay:
        if selected is None:
            revisions = sorted({item.revision for item in all_evidence})
            problems = [series_problem or "SERIES_NOT_FOUND"]
            if not all_evidence:
                problems.append("EVIDENCE_ABSENT")
            elif any(item.evidence_state == "AVAILABLE" for item in all_evidence):
                problems.append(
                    "SERIES_SELECTION_REQUIRED"
                    if series_problem == "SERIES_AMBIGUOUS"
                    else "PREPARED_SERIES_REQUIRED"
                )
            else:
                problems.append("EVIDENCE_UNAVAILABLE")
            return AdultMessageWindowDay(
                date=day,
                period=period,
                evidence_available=any(
                    item.evidence_state == "AVAILABLE" for item in all_evidence
                ),
                raw_evidence_state=(
                    "AVAILABLE"
                    if any(item.evidence_state == "AVAILABLE" for item in all_evidence)
                    else "UNAVAILABLE"
                    if all_evidence
                    else "ABSENT"
                ),
                unavailable_reason=(
                    all_evidence[-1].unavailable_reason if all_evidence else None
                ),
                evidence_id=None,
                revision=None,
                available_revisions=revisions,
                f12_status="MISSING",
                f12_quality_flags=[],
                minutes=None,
                preparation_id=None,
                historical_submission_id=None,
                historical_submission_status=None,
                c1_evaluation_id=None,
                candidate_evidence_count=len(all_evidence),
                problems=problems,
            )

        matching = [
            item
            for item in all_evidence
            if item.device_id == selected.device_id
            and item.time_zone == selected.time_zone
            and (
                item.preparation is None
                or (
                    item.preparation.series_key == selected.series_id
                    and item.preparation.measurement_version
                    == selected.measurement_version
                )
            )
        ]
        if not matching:
            problems = ["EVIDENCE_ABSENT"]
            if all_evidence:
                problems.append("EVIDENCE_ONLY_OTHER_SERIES")
            return cls._missing_day(
                day,
                period=period,
                problems=problems,
                candidate_evidence_count=len(all_evidence),
            )

        matching.sort(key=lambda item: item.revision)
        latest = matching[-1]
        revisions = [item.revision for item in matching]
        common = dict(
            date=day,
            period=period,
            evidence_available=latest.evidence_state == "AVAILABLE",
            raw_evidence_state=latest.evidence_state,
            unavailable_reason=latest.unavailable_reason,
            evidence_id=latest.id,
            revision=latest.revision,
            available_revisions=revisions,
            candidate_evidence_count=len(all_evidence),
        )
        if latest.revision != 1:
            return AdultMessageWindowDay(
                **common,
                f12_status="MISSING",
                f12_quality_flags=[],
                minutes=None,
                preparation_id=None,
                historical_submission_id=None,
                historical_submission_status=None,
                c1_evaluation_id=None,
                problems=["CORRECTION_POLICY_PENDING"],
            )
        if latest.evidence_state == "UNAVAILABLE":
            return AdultMessageWindowDay(
                **common,
                f12_status="MISSING",
                f12_quality_flags=[],
                minutes=None,
                preparation_id=None,
                historical_submission_id=None,
                historical_submission_status=None,
                c1_evaluation_id=None,
                problems=["EVIDENCE_UNAVAILABLE"],
            )
        if latest.expires_at is not None and cls._utc(latest.expires_at) <= datetime.now(UTC):
            return AdultMessageWindowDay(
                **common,
                f12_status="MISSING",
                f12_quality_flags=[],
                minutes=None,
                preparation_id=None,
                historical_submission_id=None,
                historical_submission_status=None,
                c1_evaluation_id=None,
                problems=["EVIDENCE_EXPIRED"],
            )
        preparation = latest.preparation
        if preparation is None:
            return AdultMessageWindowDay(
                **common,
                f12_status="MISSING",
                f12_quality_flags=[],
                minutes=None,
                preparation_id=None,
                historical_submission_id=None,
                historical_submission_status=None,
                c1_evaluation_id=None,
                problems=["PREPARATION_MISSING"],
            )
        try:
            prepared = HistoricalSubmissionCreate.model_validate(
                preparation.submission_body
            )
        except ValidationError:
            return AdultMessageWindowDay(
                **common,
                f12_status="MISSING",
                f12_quality_flags=[],
                minutes=None,
                preparation_id=preparation.id,
                historical_submission_id=None,
                historical_submission_status=None,
                c1_evaluation_id=None,
                problems=["PREPARATION_INVALID"],
            )
        representation = prepared.representation
        if (
            representation.subject_id != selected.series_id
            or prepared.time_zone != selected.time_zone
            or prepared.measurement_version != selected.measurement_version
        ):
            return AdultMessageWindowDay(
                **common,
                f12_status="MISSING",
                f12_quality_flags=[],
                minutes=None,
                preparation_id=preparation.id,
                historical_submission_id=None,
                historical_submission_status=None,
                c1_evaluation_id=None,
                problems=["PREPARATION_SERIES_MISMATCH"],
            )
        submission = preparation.historical_submission
        c1_id = (
            submission.analytics_evaluation_id
            if submission is not None
            and submission.status == HistoricalSubmissionStatus.PROCESSED
            else None
        )
        quality = cls._quality_decision(latest, preparation)
        return AdultMessageWindowDay(
            **common,
            f12_status=representation.status,
            f12_quality_flags=list(representation.quality_flags),
            minutes=(
                float(representation.value)
                if representation.status in {"OBSERVED", "OBSERVED_ZERO"}
                else None
            ),
            preparation_id=preparation.id,
            historical_submission_id=submission.id if submission else None,
            historical_submission_status=(submission.status.value if submission else None),
            c1_evaluation_id=c1_id,
            quality_verified=quality[0],
            quality_policy_version=quality[1],
            quality_reason_codes=quality[2],
            quality_evaluated_at=quality[3],
            problems=[
                *([] if quality[0] else [f"F12_QUALITY:{code}" for code in quality[2]]),
                *[
                    f"F12_QUALITY_FLAG:{flag}"
                    for flag in representation.quality_flags
                ],
            ],
        )

    @staticmethod
    def _missing_day(
        day: date,
        *,
        period: str,
        problems: list[str],
        candidate_evidence_count: int,
    ) -> AdultMessageWindowDay:
        return AdultMessageWindowDay(
            date=day,
            period=period,
            evidence_available=False,
            raw_evidence_state="ABSENT",
            unavailable_reason=None,
            evidence_id=None,
            revision=None,
            available_revisions=[],
            f12_status="MISSING",
            f12_quality_flags=[],
            minutes=None,
            preparation_id=None,
            historical_submission_id=None,
            historical_submission_status=None,
            c1_evaluation_id=None,
            candidate_evidence_count=candidate_evidence_count,
            problems=problems,
        )

    @staticmethod
    def _declarations(
        contexts,
        weekly_declarations,
        consents,
        *,
        selected,
        dates,
        current_device: bool,
        current_shared_phone: bool,
    ) -> AdultMessageDeclarationDiagnostic:
        context_records = [
            AdultMessageContextRecord(
                type=item.type.value,
                start_date=item.start_date,
                end_date=item.end_date,
            )
            for item in contexts
        ]
        relevant = [
            item
            for item in weekly_declarations
            if selected is not None
            and item.device_id == selected.device_id
            and item.time_zone == selected.time_zone
        ]
        return AdultMessageDeclarationDiagnostic(
            assignment=AdultMessageWindowService._declaration_coverage(
                relevant,
                dates,
                field="assignment_verified",
                current_state_present=current_device,
                missing_problem="ASSIGNMENT_HISTORY_MISSING",
                changed_problem="ASSIGNMENT_CHANGED_WITHIN_WINDOW",
                disallowed_value=False,
                disallowed_problem="ASSIGNMENT_NOT_VERIFIED",
            ),
            shared_phone=AdultMessageWindowService._declaration_coverage(
                relevant,
                dates,
                field="shared_phone",
                current_state_present=current_shared_phone,
                missing_problem="SHARED_PHONE_HISTORY_MISSING",
                changed_problem="SHARED_PHONE_CHANGED_WITHIN_WINDOW",
                disallowed_value=True,
                disallowed_problem="SHARED_PHONE_DECLARED",
            ),
            family_context=AdultMessageWindowService._declaration_coverage(
                relevant,
                dates,
                field="context_declared",
                current_state_present=bool(context_records),
                missing_problem="CONTEXT_HISTORY_MISSING",
                changed_problem="CONTEXT_CHANGED_WITHIN_WINDOW",
            ),
            consent=AdultMessageWindowService._consent_coverage(
                consents,
                dates,
                time_zone=selected.time_zone if selected is not None else None,
            ),
            context_records=context_records,
        )

    @staticmethod
    def _declaration_coverage(
        declarations,
        dates,
        *,
        field,
        current_state_present,
        missing_problem,
        changed_problem,
        disallowed_value=None,
        disallowed_problem=None,
    ) -> AdultMessageHistoricalDeclaration:
        selected_by_date = {}
        for day in dates:
            applicable = [
                item
                for item in declarations
                if item.effective_from <= day
                and (item.effective_to is None or item.effective_to >= day)
                and (
                    item.revoked_effective_date is None
                    or item.revoked_effective_date > day
                )
            ]
            if applicable:
                selected_by_date[day] = max(
                    applicable, key=lambda item: item.revision
                )
        covered_dates = sorted(selected_by_date)
        missing_dates = [day for day in dates if day not in selected_by_date]
        values = {
            bool(getattr(item, field)) for item in selected_by_date.values()
        }
        contract_value = next(iter(values)) if len(values) == 1 else None
        if missing_dates:
            problem = missing_problem
        elif len(values) != 1:
            problem = changed_problem
        elif disallowed_problem is not None and contract_value == disallowed_value:
            problem = disallowed_problem
        else:
            problem = None
        return AdultMessageHistoricalDeclaration(
            contract_value=contract_value,
            historical_records_present=bool(declarations),
            complete_for_window=not missing_dates,
            current_state_present=current_state_present,
            covered_dates=covered_dates,
            missing_dates=missing_dates,
            revisions=sorted({item.revision for item in selected_by_date.values()}),
            problem=problem,
        )

    @staticmethod
    def _consent_coverage(consents, dates, *, time_zone):
        if time_zone is None:
            return AdultMessageHistoricalDeclaration(
                contract_value=None,
                historical_records_present=bool(consents),
                complete_for_window=False,
                current_state_present=False,
                covered_dates=[],
                missing_dates=list(dates),
                revisions=[],
                problem="CONSENT_HISTORY_MISSING",
            )
        from zoneinfo import ZoneInfo

        zone = ZoneInfo(time_zone)
        covered_dates = []
        for day in dates:
            start = datetime.combine(day, datetime.min.time(), tzinfo=zone).astimezone(UTC)
            end = datetime.combine(
                day + timedelta(days=1), datetime.min.time(), tzinfo=zone
            ).astimezone(UTC)
            intervals = sorted(
                [
                    (
                        AdultMessageWindowService._utc(item.granted_at),
                        (
                            AdultMessageWindowService._utc(item.revoked_at)
                            if item.revoked_at is not None
                            else None
                        ),
                    )
                    for item in consents
                    if AdultMessageWindowService._utc(item.granted_at) < end
                    and (
                        item.revoked_at is None
                        or AdultMessageWindowService._utc(item.revoked_at) > start
                    )
                ],
                key=lambda item: item[0],
            )
            covered_until = start
            for interval_start, interval_end in intervals:
                if interval_start > covered_until:
                    break
                if interval_end is None:
                    covered_until = end
                    break
                covered_until = max(covered_until, interval_end)
                if covered_until >= end:
                    break
            if covered_until >= end:
                covered_dates.append(day)
        missing_dates = [day for day in dates if day not in covered_dates]
        return AdultMessageHistoricalDeclaration(
            contract_value=True if not missing_dates else None,
            historical_records_present=bool(consents),
            complete_for_window=not missing_dates,
            current_state_present=any(item.revoked_at is None for item in consents),
            covered_dates=covered_dates,
            missing_dates=missing_dates,
            revisions=[],
            problem="CONSENT_HISTORY_MISSING" if missing_dates else None,
        )

    @staticmethod
    def _blockers(days, candidates, selected, declarations):
        blockers = []
        if selected is None:
            blockers.append(
                AdultMessageWindowBlocker(
                    code="SERIES_AMBIGUOUS" if len(candidates) > 1 else "SERIES_NOT_FOUND",
                    dates=[],
                    detail=(
                        "More than one F12 series is present in the window"
                        if len(candidates) > 1
                        else "No prepared F12 series exists in the window"
                    ),
                )
            )
        missing = [
            item.date
            for item in days
            if item.f12_status in {"MISSING", "NOT_APPLICABLE"}
        ]
        if missing:
            blockers.append(
                AdultMessageWindowBlocker(
                    code="F12_DAYS_UNAVAILABLE",
                    dates=missing,
                    detail="One or more daily F12 representations are unavailable",
                )
            )
        corrections = [
            item.date for item in days if "CORRECTION_POLICY_PENDING" in item.problems
        ]
        if corrections:
            blockers.append(
                AdultMessageWindowBlocker(
                    code="F12_CORRECTIONS_PENDING",
                    dates=corrections,
                    detail="Corrected revisions cannot be selected under the current policy",
                )
            )
        unverified_quality = [item.date for item in days if not item.quality_verified]
        if unverified_quality:
            blockers.append(
                AdultMessageWindowBlocker(
                    code="F12_QUALITY_NOT_VERIFIED",
                    dates=unverified_quality,
                    detail=(
                        "One or more daily originals fail the operational quality policy"
                    ),
                )
            )
        for declaration in (
            declarations.assignment,
            declarations.shared_phone,
            declarations.family_context,
            declarations.consent,
        ):
            if declaration.problem is not None:
                blockers.append(
                    AdultMessageWindowBlocker(
                        code=declaration.problem,
                        dates=declaration.missing_dates,
                        detail=(
                            "A historical declaration cannot be derived from current state"
                        ),
                    )
                )
        return blockers

    @staticmethod
    def _quality_decision(evidence, preparation):
        raw = preparation.audit.get("operational_quality")
        if not isinstance(raw, dict):
            return False, None, ["QUALITY_NOT_EVALUATED"], None
        policy = raw.get("policy_version")
        reasons = raw.get("reason_codes")
        checks = raw.get("checks")
        required_checks = (
            "original_integrity",
            "authenticated_binding",
            "local_day_closed",
            "captured_next_local_day",
            "initial_state_known",
            "continuous_query_coverage",
            "device_lifecycle_clear",
            "reconstruction_valid",
            "revision_unambiguous",
        )
        bindings_match = (
            raw.get("schema_version") == 1
            and policy == F12_OPERATIONAL_QUALITY_POLICY_VERSION
            and raw.get("evidence_id") == str(evidence.id)
            and raw.get("capture_id") == evidence.capture_id
            and raw.get("preparation_id") == str(preparation.id)
            and raw.get("profile_id") == str(evidence.profile_id)
            and raw.get("device_id") == str(evidence.device_id)
            and raw.get("local_date") == evidence.local_date.isoformat()
            and raw.get("time_zone") == evidence.time_zone
            and raw.get("revision") == evidence.revision
            and raw.get("evidence_content_sha256") == evidence.content_sha256
            and isinstance(reasons, list)
            and all(isinstance(item, str) and item for item in reasons)
            and isinstance(checks, dict)
            and all(type(checks.get(name)) is bool for name in required_checks)
        )
        try:
            evaluated_at = datetime.fromisoformat(raw["evaluated_at"])
            if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            bindings_match = False
            evaluated_at = None
        if not bindings_match or type(raw.get("quality_verified")) is not bool:
            return False, policy if isinstance(policy, str) else None, ["QUALITY_AUDIT_INVALID"], evaluated_at
        verified = (
            raw["quality_verified"] is True
            and reasons == ["QUALITY_VERIFIED"]
            and all(checks.get(name) is True for name in required_checks)
        )
        return verified, policy, list(reasons), evaluated_at

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
