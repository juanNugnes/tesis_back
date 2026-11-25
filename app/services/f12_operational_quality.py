from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.db.models import F12RawEvidenceCapture
from app.schemas.historical_submission import HistoricalSubmissionCreate
from app.services.f12_evidence_validator import (
    F12EvidencePairBytes,
    F12EvidenceValidationError,
    _midnight_epoch_millis,
    _parse_pair,
    _validate_overlaps,
    expected_capture_id,
)


F12_OPERATIONAL_QUALITY_POLICY_VERSION = "f12-operational-quality-v1"


@dataclass(frozen=True)
class F12OperationalQualityAssessment:
    quality_verified: bool
    reason_codes: tuple[str, ...]
    checks: dict[str, Any]

    def audit_payload(
        self,
        *,
        evidence: F12RawEvidenceCapture,
        preparation_id: uuid.UUID,
        evaluated_at: datetime,
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "policy_version": F12_OPERATIONAL_QUALITY_POLICY_VERSION,
            "quality_verified": self.quality_verified,
            "reason_codes": list(self.reason_codes),
            "checks": self.checks,
            "evaluated_at": _as_utc(evaluated_at).isoformat(),
            "evidence_id": str(evidence.id),
            "capture_id": evidence.capture_id,
            "preparation_id": str(preparation_id),
            "profile_id": str(evidence.profile_id),
            "device_id": str(evidence.device_id),
            "local_date": evidence.local_date.isoformat(),
            "time_zone": evidence.time_zone,
            "revision": evidence.revision,
            "evidence_content_sha256": evidence.content_sha256,
            "source_created_at": _as_utc(evidence.source_created_at).isoformat(),
            "received_at": _as_utc(evidence.received_at).isoformat(),
        }


class F12OperationalQualityPolicy:
    """Conservative quality decision over exact persisted Android originals."""

    @classmethod
    def evaluate(
        cls,
        *,
        evidence: F12RawEvidenceCapture,
        prepared: HistoricalSubmissionCreate,
        correction_pending: bool,
    ) -> F12OperationalQualityAssessment:
        reasons: set[str] = set()
        checks: dict[str, Any] = {
            "original_integrity": False,
            "authenticated_binding": False,
            "local_day_closed": False,
            "captured_next_local_day": False,
            "initial_state_known": False,
            "continuous_query_coverage": False,
            "device_lifecycle_clear": False,
            "reconstruction_valid": False,
            "revision_unambiguous": False,
        }

        if evidence.evidence_state != "AVAILABLE" or evidence.receipt_status != "RECEIVED":
            reasons.add("EVIDENCE_NOT_AVAILABLE")
        if evidence.revision != 1 or correction_pending:
            reasons.add("CORRECTION_POLICY_PENDING")
        else:
            checks["revision_unambiguous"] = True

        parsed = cls._parse_and_verify_originals(evidence, reasons, checks)
        if parsed is None:
            return cls._result(reasons, checks)
        summaries, events = parsed

        try:
            zone = ZoneInfo(evidence.time_zone)
            day_start = _midnight_epoch_millis(evidence.local_date, zone)
            day_end = _midnight_epoch_millis(
                evidence.local_date + timedelta(days=1), zone
            )
        except (ValueError, F12EvidenceValidationError):
            reasons.add("LOCAL_DAY_BOUNDARY_INVALID")
            return cls._result(reasons, checks)

        source_created_at = _as_utc(evidence.source_created_at)
        source_local_date = source_created_at.astimezone(zone).date()
        checks["source_capture_local_date"] = source_local_date.isoformat()
        checks["receipt_local_date"] = _as_utc(evidence.received_at).astimezone(
            zone
        ).date().isoformat()
        if source_local_date == evidence.local_date + timedelta(days=1):
            checks["captured_next_local_day"] = True
        else:
            reasons.add("SOURCE_CAPTURE_OUTSIDE_NEXT_LOCAL_DAY")

        if source_created_at.timestamp() * 1000 < max(
            summary["collectedAtEpochMs"] for summary in summaries
        ):
            reasons.add("SOURCE_CAPTURE_TIME_INCONSISTENT")

        exact_day_queries = [
            summary
            for summary in summaries
            if summary["requestedBeginEpochMs"] == day_start
            and summary["requestedEndEpochMs"] == day_end
        ]
        if exact_day_queries:
            checks["local_day_closed"] = True
        else:
            reasons.add("COMPLETE_LOCAL_DAY_QUERY_MISSING")

        screen_events = [
            row
            for row in events
            if row["eventTypeName"]
            in {"SCREEN_INTERACTIVE", "SCREEN_NON_INTERACTIVE"}
        ]
        states_by_time: dict[int, set[str]] = {}
        for row in screen_events:
            states_by_time.setdefault(row["eventTimeEpochMs"], set()).add(
                row["eventTypeName"]
            )
        if any(len(states) > 1 for states in states_by_time.values()):
            reasons.add("CONTRADICTORY_SCREEN_STATES")

        prior_states = [
            row for row in screen_events if row["eventTimeEpochMs"] < day_start
        ]
        initial = (
            max(prior_states, key=lambda row: row["eventTimeEpochMs"])
            if prior_states
            else None
        )
        if initial is None:
            reasons.add("INITIAL_SCREEN_STATE_UNKNOWN")
            coverage_start = day_start
        else:
            checks["initial_state_known"] = True
            checks["initial_state"] = initial["eventTypeName"]
            checks["initial_state_epoch_ms"] = initial["eventTimeEpochMs"]
            coverage_start = initial["eventTimeEpochMs"]

        successful = [
            summary
            for summary in summaries
            if summary["usageAccessAvailable"]
            and not summary["queryReturnedNull"]
            and summary["errorCode"] is None
        ]
        if cls._has_continuous_coverage(
            successful,
            begin=coverage_start,
            end=day_end,
        ):
            checks["continuous_query_coverage"] = True
        else:
            reasons.add("CONTINUOUS_QUERY_COVERAGE_INCOMPLETE")

        lifecycle = [
            row
            for row in events
            if row["eventTypeName"] in {"DEVICE_SHUTDOWN", "DEVICE_STARTUP"}
            and coverage_start <= row["eventTimeEpochMs"] < day_end
        ]
        if lifecycle:
            reasons.add("DEVICE_LIFECYCLE_UNRESOLVED")
        else:
            checks["device_lifecycle_clear"] = True

        representation = prepared.representation
        expected_start = datetime.fromtimestamp(day_start / 1000, tz=UTC)
        expected_end = datetime.fromtimestamp(day_end / 1000, tz=UTC)
        if (
            prepared.time_zone == evidence.time_zone
            and representation.interval_start == expected_start
            and representation.interval_end == expected_end
            and representation.computed_at == source_created_at
            and representation.status in {"OBSERVED", "OBSERVED_ZERO"}
            and not representation.quality_flags
        ):
            checks["reconstruction_valid"] = True
            checks["reconstruction_status"] = representation.status
        else:
            reasons.add("RECONSTRUCTION_NOT_VERIFIABLE")

        return cls._result(reasons, checks)

    @staticmethod
    def _parse_and_verify_originals(evidence, reasons, checks):
        ordered = sorted(evidence.pairs, key=lambda item: item.ordinal)
        if (
            evidence.pair_count != len(ordered)
            or [item.ordinal for item in ordered] != list(range(len(ordered)))
            or not ordered
        ):
            reasons.add("ORIGINAL_PAIR_SET_INVALID")
            return None

        total_bytes = 0
        content_digest = hashlib.sha256()
        parsed = []
        run_ids = set()
        hardware = set()
        try:
            for item in ordered:
                summary_bytes = bytes(item.summary_bytes)
                events_bytes = bytes(item.events_bytes)
                if (
                    item.summary_size != len(summary_bytes)
                    or item.events_size != len(events_bytes)
                    or hashlib.sha256(summary_bytes).hexdigest()
                    != item.summary_sha256
                    or hashlib.sha256(events_bytes).hexdigest()
                    != item.events_sha256
                ):
                    raise F12EvidenceValidationError(
                        "stored_integrity_mismatch", "Stored original metadata mismatch"
                    )
                pair = F12EvidencePairBytes(
                    collector_run_id=item.collector_run_id,
                    summary_bytes=summary_bytes,
                    events_bytes=events_bytes,
                    summary_sha256=item.summary_sha256,
                    events_sha256=item.events_sha256,
                )
                summary, rows, detected_hardware = _parse_pair(pair)
                if summary["collectorRunId"] in run_ids:
                    raise F12EvidenceValidationError(
                        "duplicate_collector_run", "Duplicate collector run"
                    )
                run_ids.add(summary["collectorRunId"])
                hardware.update(detected_hardware)
                parsed.append((summary, rows))
                total_bytes += len(summary_bytes) + len(events_bytes)
                content_digest.update(summary_bytes)
                content_digest.update(b"\0")
                content_digest.update(events_bytes)
                content_digest.update(b"\0")
            _validate_overlaps(parsed)
            if len(hardware) > 1:
                raise F12EvidenceValidationError(
                    "mixed_hardware", "Stored originals contain mixed hardware"
                )
        except (F12EvidenceValidationError, ValueError, TypeError):
            reasons.add("ORIGINAL_INTEGRITY_INVALID")
            return None

        content_sha256 = content_digest.hexdigest()
        if (
            total_bytes != evidence.total_bytes
            or content_sha256 != evidence.content_sha256
        ):
            reasons.add("ORIGINAL_INTEGRITY_INVALID")
            return None

        expected_id = expected_capture_id(
            evidence.profile_id,
            evidence.device_id,
            evidence.local_date,
            evidence.revision,
            content_sha256,
        )
        if expected_id != evidence.capture_id:
            reasons.add("AUTHENTICATED_BINDING_INVALID")
            return None

        checks["original_integrity"] = True
        checks["authenticated_binding"] = True
        checks["collector_run_count"] = len(parsed)
        checks["event_count"] = sum(len(rows) for _summary, rows in parsed)
        return (
            [summary for summary, _rows in parsed],
            [row for _summary, rows in parsed for row in rows],
        )

    @staticmethod
    def _has_continuous_coverage(summaries, *, begin: int, end: int) -> bool:
        covered_until = begin
        windows = sorted(
            (
                summary["requestedBeginEpochMs"],
                summary["requestedEndEpochMs"],
            )
            for summary in summaries
        )
        for window_begin, window_end in windows:
            if window_end <= covered_until:
                continue
            if window_begin > covered_until:
                return False
            covered_until = max(covered_until, window_end)
            if covered_until >= end:
                return True
        return False

    @staticmethod
    def _result(reasons, checks):
        verified = not reasons and all(
            checks[name]
            for name in (
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
        )
        return F12OperationalQualityAssessment(
            quality_verified=verified,
            reason_codes=("QUALITY_VERIFIED",) if verified else tuple(sorted(reasons)),
            checks=checks,
        )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
