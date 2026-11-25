import hashlib
import json
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from app.db.models import F12RawEvidenceCapture, F12RawEvidencePair
from app.repositories.f12_preparation_repository import F12PreparationRepository
from app.schemas.historical_submission import (
    HISTORICAL_EXPORT_ADAPTER_VERSION,
    HISTORICAL_MEASUREMENT_VERSION,
    HISTORICAL_REPRESENTATION_SPEC_ID,
    HISTORICAL_SOURCE_ID,
    HISTORICAL_SOURCE_SEMANTICS,
    HistoricalSubmissionCreate,
)
from app.services.f12_evidence_validator import expected_capture_id
from app.services.f12_operational_quality import (
    F12_OPERATIONAL_QUALITY_POLICY_VERSION,
    F12OperationalQualityPolicy,
)
from app.services.f12_preparation_service import (
    F12PreparationIneligibleError,
    F12PreparationService,
)


DAY = date(2026, 1, 5)
DAY_START = int(datetime(2026, 1, 5, tzinfo=UTC).timestamp() * 1000)
DAY_END = int(datetime(2026, 1, 6, tzinfo=UTC).timestamp() * 1000)


def _event(run_id, begin, end, collected, when, code, ordinal):
    names = {
        15: "SCREEN_INTERACTIVE",
        16: "SCREEN_NON_INTERACTIVE",
        26: "DEVICE_SHUTDOWN",
        27: "DEVICE_STARTUP",
    }
    return {
        "schemaVersion": "raw-usage-event-v1",
        "collectorRunId": run_id,
        "queryBeginEpochMs": begin,
        "queryEndEpochMs": end,
        "collectedAtEpochMs": collected,
        "eventTimeEpochMs": when,
        "eventTypeCode": code,
        "eventTypeName": names[code],
        "queryOrdinal": ordinal,
        "packageName": None,
        "className": None,
        "androidApiLevel": 35,
        "manufacturer": "SYNTHETIC",
        "model": "NOT-A-PHONE",
    }


def _pair(begin, end, events, *, error_code=None, run_id=None):
    run_id = run_id or str(uuid.uuid4())
    collected = DAY_END + 60_000
    rows = [
        _event(run_id, begin, end, collected, when, code, ordinal)
        for ordinal, (when, code) in enumerate(events)
    ]
    summary = {
        "collectorRunId": run_id,
        "requestedBeginEpochMs": begin,
        "requestedEndEpochMs": end,
        "collectedAtEpochMs": collected,
        "usageAccessAvailable": True,
        "queryReturnedNull": False,
        "eventCount": len(rows),
        "errorCode": error_code,
        "errorMessage": "synthetic auxiliary failure" if error_code else None,
    }
    if error_code:
        rows = []
        summary["eventCount"] = 0
    summary_bytes = json.dumps(summary, separators=(",", ":")).encode()
    events_bytes = b"".join(
        json.dumps(row, separators=(",", ":")).encode() + b"\n" for row in rows
    )
    return summary_bytes, events_bytes, run_id


def quality_fixture(
    *,
    status="OBSERVED",
    include_prior=True,
    prior_query_end=DAY_START,
    lifecycle=False,
    auxiliary_error=False,
    source_day_offset=1,
    received_day_offset=3,
    revision=1,
):
    evidence_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    device_id = uuid.uuid4()
    source_created_at = datetime(2026, 1, 5, tzinfo=UTC) + timedelta(
        days=source_day_offset, minutes=2
    )
    pairs = []
    if include_prior:
        pairs.append(
            _pair(
                DAY_START - 2 * 60 * 60 * 1000,
                prior_query_end,
                [(DAY_START - 60 * 60 * 1000, 16)],
            )
        )
    day_events = []
    minutes = 0.0
    if status == "OBSERVED":
        day_events.extend(
            [
                (DAY_START + 60 * 60 * 1000, 15),
                (DAY_START + 2 * 60 * 60 * 1000, 16),
            ]
        )
        minutes = 60.0
    if lifecycle:
        day_events.append((DAY_START + 3 * 60 * 60 * 1000, 27))
        day_events.sort()
    pairs.append(_pair(DAY_START, DAY_END, day_events))
    if auxiliary_error:
        pairs.append(
            _pair(
                DAY_START + 3 * 60 * 60 * 1000,
                DAY_START + 4 * 60 * 60 * 1000,
                [],
                error_code="QUERY_RUNTIME_EXCEPTION",
            )
        )

    content = hashlib.sha256()
    models = []
    for ordinal, (summary_bytes, events_bytes, run_id) in enumerate(pairs):
        content.update(summary_bytes)
        content.update(b"\0")
        content.update(events_bytes)
        content.update(b"\0")
        models.append(
            F12RawEvidencePair(
                capture_id=evidence_id,
                ordinal=ordinal,
                collector_run_id=run_id,
                summary_sha256=hashlib.sha256(summary_bytes).hexdigest(),
                events_sha256=hashlib.sha256(events_bytes).hexdigest(),
                summary_size=len(summary_bytes),
                events_size=len(events_bytes),
                summary_bytes=summary_bytes,
                events_bytes=events_bytes,
            )
        )
    content_sha256 = content.hexdigest()
    capture = F12RawEvidenceCapture(
        id=evidence_id,
        capture_id=expected_capture_id(
            profile_id, device_id, DAY, revision, content_sha256
        ),
        profile_id=profile_id,
        device_id=device_id,
        local_date=DAY,
        time_zone="UTC",
        revision=revision,
        envelope_version=1,
        adapter_version=HISTORICAL_EXPORT_ADAPTER_VERSION,
        evidence_state="AVAILABLE",
        unavailable_reason=None,
        content_sha256=content_sha256,
        pair_count=len(models),
        total_bytes=sum(item.summary_size + item.events_size for item in models),
        source_created_at=source_created_at,
        received_at=datetime(2026, 1, 5, tzinfo=UTC)
        + timedelta(days=received_day_offset),
        receipt_status="RECEIVED",
        pairs=models,
    )
    series_id = "series-" + "a" * 64
    prepared_status = "MISSING" if lifecycle else status
    body = HistoricalSubmissionCreate.model_validate(
        {
            "request_id": f"f12prep-{capture.capture_id}",
            "time_zone": "UTC",
            "measurement_version": HISTORICAL_MEASUREMENT_VERSION,
            "representation": {
                "representation_record_id": "rep-" + "b" * 64,
                "representation_spec_id": HISTORICAL_REPRESENTATION_SPEC_ID,
                "subject_id": series_id,
                "phenomenon": "DAILY_USE_DURATION",
                "interval_start": datetime(2026, 1, 5, tzinfo=UTC).isoformat(),
                "interval_end": datetime(2026, 1, 6, tzinfo=UTC).isoformat(),
                "value": None if prepared_status == "MISSING" else minutes,
                "unit": "minutes",
                "status": prepared_status,
                "coverage": {
                    "value": None,
                    "basis": "daily_capture_completeness_not_quantified",
                },
                "quality_flags": (
                    ["DEVICE_LIFECYCLE_REVIEW_REQUIRED"]
                    if lifecycle
                    else []
                ),
                "provenance": {
                    "source_id": HISTORICAL_SOURCE_ID,
                    "source_version": HISTORICAL_EXPORT_ADAPTER_VERSION,
                    "source_semantics": HISTORICAL_SOURCE_SEMANTICS,
                    "adapter_version": "v2",
                    "input_fingerprint": "c" * 64,
                },
                "computed_at": source_created_at.isoformat(),
            },
        }
    )
    return capture, body


def assess(capture, prepared, *, correction_pending=False):
    return F12OperationalQualityPolicy.evaluate(
        evidence=capture,
        prepared=prepared,
        correction_pending=correction_pending,
    )


@pytest.mark.parametrize("status", ["OBSERVED", "OBSERVED_ZERO"])
def test_valid_observed_and_zero_days_are_verified(status):
    capture, prepared = quality_fixture(status=status)

    result = assess(capture, prepared)

    assert result.quality_verified is True
    assert result.reason_codes == ("QUALITY_VERIFIED",)
    assert result.checks["reconstruction_status"] == status


def test_initial_state_and_empty_query_without_zero_proof_are_rejected():
    capture, prepared = quality_fixture(status="OBSERVED_ZERO", include_prior=False)

    result = assess(capture, prepared)

    assert result.quality_verified is False
    assert "INITIAL_SCREEN_STATE_UNKNOWN" in result.reason_codes


def test_incomplete_query_coverage_is_rejected():
    capture, prepared = quality_fixture(
        prior_query_end=DAY_START - 10 * 60 * 1000
    )

    result = assess(capture, prepared)

    assert "CONTINUOUS_QUERY_COVERAGE_INCOMPLETE" in result.reason_codes


def test_failed_auxiliary_query_does_not_override_valid_coverage():
    capture, prepared = quality_fixture(auxiliary_error=True)

    result = assess(capture, prepared)

    assert result.quality_verified is True


def test_unresolved_restart_and_corrupted_originals_are_rejected():
    restart_capture, restart_prepared = quality_fixture(lifecycle=True)
    restart = assess(restart_capture, restart_prepared)
    assert "DEVICE_LIFECYCLE_UNRESOLVED" in restart.reason_codes

    corrupt_capture, corrupt_prepared = quality_fixture()
    corrupt_capture.pairs[0].summary_sha256 = "0" * 64
    corrupt = assess(corrupt_capture, corrupt_prepared)
    assert corrupt.reason_codes == ("ORIGINAL_INTEGRITY_INVALID",)


def test_capture_must_occur_next_local_day_but_late_receipt_is_allowed():
    timely_capture, timely_prepared = quality_fixture(received_day_offset=8)
    retrospective_capture, retrospective_prepared = quality_fixture(
        source_day_offset=2,
        received_day_offset=2,
    )

    timely = assess(timely_capture, timely_prepared)
    retrospective = assess(retrospective_capture, retrospective_prepared)

    assert timely.quality_verified is True
    assert timely.checks["receipt_local_date"] == "2026-01-13"
    assert "SOURCE_CAPTURE_OUTSIDE_NEXT_LOCAL_DAY" in retrospective.reason_codes


def test_pending_correction_keeps_revision_one_unverified():
    capture, prepared = quality_fixture()

    result = assess(capture, prepared, correction_pending=True)

    assert "CORRECTION_POLICY_PENDING" in result.reason_codes


@pytest.mark.asyncio
async def test_preparation_persists_versioned_quality_audit(db_session):
    capture, _prepared = quality_fixture()
    db_session.add(capture)
    await db_session.commit()

    preparation = await F12PreparationService(
        F12PreparationRepository(db_session)
    ).prepare(
        evidence_id=capture.id,
        profile_id=capture.profile_id,
        device_id=capture.device_id,
    )

    quality = preparation.audit["operational_quality"]
    assert quality["policy_version"] == F12_OPERATIONAL_QUALITY_POLICY_VERSION
    assert quality["quality_verified"] is True
    assert quality["reason_codes"] == ["QUALITY_VERIFIED"]
    assert quality["evidence_id"] == str(capture.id)
    assert quality["preparation_id"] == str(preparation.id)
    assert quality["revision"] == 1


@pytest.mark.asyncio
async def test_preparation_refuses_revision_one_when_correction_exists(db_session):
    first, _prepared = quality_fixture()
    correction, _ = quality_fixture(revision=2)
    correction.profile_id = first.profile_id
    correction.device_id = first.device_id
    correction.capture_id = expected_capture_id(
        first.profile_id,
        first.device_id,
        correction.local_date,
        correction.revision,
        correction.content_sha256,
    )
    db_session.add_all([first, correction])
    await db_session.commit()

    with pytest.raises(F12PreparationIneligibleError) as error:
        await F12PreparationService(F12PreparationRepository(db_session)).prepare(
            evidence_id=first.id,
            profile_id=first.profile_id,
            device_id=first.device_id,
        )

    assert error.value.code == "corrected_revision_pending"
