import hashlib
import uuid
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import func, select

from app.db.models import (
    AdultAccount,
    AdultMessageEvaluationJob,
    AutomaticOfferSettings,
    Avatar,
    Device,
    F12EvidencePreparation,
    F12RawEvidenceCapture,
    HistoricalSubmission,
    HistoricalSubmissionStatus,
    Profile,
    ProfileContext,
    ProfileContextType,
)
from app.repositories.adult_message_window_repository import (
    AdultMessageWindowRepository,
)
from app.schemas.historical_submission import (
    HISTORICAL_BRIDGE_VERSION,
    HISTORICAL_EXPORT_ADAPTER_VERSION,
    HISTORICAL_MEASUREMENT_VERSION,
    HISTORICAL_REPRESENTATION_SPEC_ID,
    HISTORICAL_SOURCE_ID,
    HISTORICAL_SOURCE_SEMANTICS,
)
from app.services.adult_message_window_service import (
    AdultMessageWindowDateError,
    AdultMessageWindowProfileNotFoundError,
    AdultMessageWindowService,
)


EVALUATION_DATE = date(2026, 2, 2)
WINDOW_START = date(2026, 1, 5)
SERIES_A = "series-" + "a" * 64
SERIES_B = "series-" + "b" * 64


async def create_profile(db, *, alias="Weekly window"):
    profile = Profile(alias=alias, age=9, avatar=Avatar.fox)
    db.add(profile)
    await db.flush()
    device = Device(
        profile_id=profile.id,
        installation_id=f"weekly-{uuid.uuid4()}",
        platform="ANDROID",
    )
    db.add(device)
    await db.flush()
    return profile, device


def submission_body(day, *, series_id, status, minutes):
    begin = datetime.combine(day, time.min, tzinfo=UTC)
    end = begin + timedelta(days=1)
    return {
        "request_id": f"f12prep-{day.isoformat()}-{series_id[-4:]}",
        "time_zone": "UTC",
        "measurement_version": HISTORICAL_MEASUREMENT_VERSION,
        "representation": {
            "representation_record_id": "rep-" + hashlib.sha256(
                f"{series_id}:{day}".encode()
            ).hexdigest(),
            "representation_spec_id": HISTORICAL_REPRESENTATION_SPEC_ID,
            "subject_id": series_id,
            "phenomenon": "DAILY_USE_DURATION",
            "interval_start": begin.isoformat(),
            "interval_end": end.isoformat(),
            "value": minutes,
            "unit": "minutes",
            "status": status,
            "coverage": {
                "value": None,
                "basis": "daily_capture_completeness_not_quantified",
            },
            "quality_flags": (
                ["INCOMPLETE_DAILY_CAPTURE"] if status == "MISSING" else []
            ),
            "provenance": {
                "source_id": HISTORICAL_SOURCE_ID,
                "source_version": HISTORICAL_EXPORT_ADAPTER_VERSION,
                "source_semantics": HISTORICAL_SOURCE_SEMANTICS,
                "adapter_version": "v2",
                "input_fingerprint": hashlib.sha256(
                    f"input:{series_id}:{day}".encode()
                ).hexdigest(),
            },
            "computed_at": (end + timedelta(minutes=2)).isoformat(),
        },
    }


async def add_evidence(
    db,
    profile,
    device,
    day,
    *,
    revision=1,
    evidence_state="AVAILABLE",
    series_id=SERIES_A,
    status="OBSERVED",
    minutes=60.0,
    prepared=True,
    c1=False,
):
    seed = f"{profile.id}:{device.id}:{day}:{revision}:{evidence_state}"
    capture = F12RawEvidenceCapture(
        capture_id=hashlib.sha256(seed.encode()).hexdigest(),
        profile_id=profile.id,
        device_id=device.id,
        local_date=day,
        time_zone="UTC",
        revision=revision,
        envelope_version=1,
        adapter_version=HISTORICAL_EXPORT_ADAPTER_VERSION,
        evidence_state=evidence_state,
        unavailable_reason=("NOT_EXPORTED" if evidence_state == "UNAVAILABLE" else None),
        content_sha256=hashlib.sha256(f"content:{seed}".encode()).hexdigest(),
        pair_count=1 if evidence_state == "AVAILABLE" else 0,
        total_bytes=10 if evidence_state == "AVAILABLE" else 0,
        source_created_at=datetime.combine(
            day + timedelta(days=1), time.min, tzinfo=UTC
        ),
        receipt_status="RECEIVED",
    )
    db.add(capture)
    await db.flush()
    if not prepared:
        return capture, None

    body = submission_body(
        day,
        series_id=series_id,
        status=status,
        minutes=minutes,
    )
    submission = None
    if c1:
        representation = body["representation"]
        submission = HistoricalSubmission(
            request_id=body["request_id"],
            profile_id=profile.id,
            device_id=device.id,
            assignment_id=device.id,
            local_date=day,
            time_zone="UTC",
            measurement_version=HISTORICAL_MEASUREMENT_VERSION,
            bridge_version=HISTORICAL_BRIDGE_VERSION,
            tzdata_version="2026.5",
            series_key=series_id,
            representation_record_id=representation["representation_record_id"],
            interval_start=datetime.fromisoformat(representation["interval_start"]),
            interval_end=datetime.fromisoformat(representation["interval_end"]),
            payload={
                "request_id": body["request_id"],
                "representation": representation,
            },
            payload_sha256=hashlib.sha256(
                f"payload:{series_id}:{day}".encode()
            ).hexdigest(),
            status=HistoricalSubmissionStatus.PROCESSED,
            attempts=1,
            analytics_evaluation_id=f"eval-{day.isoformat()}",
            analytics_response={"status": "processed"},
        )
        db.add(submission)
        await db.flush()

    preparation = F12EvidencePreparation(
        evidence_id=capture.id,
        request_id=body["request_id"],
        preparer_commit="94ff3a8" * 5 + "94ff3",
        measurement_version=HISTORICAL_MEASUREMENT_VERSION,
        bridge_version=HISTORICAL_BRIDGE_VERSION,
        export_adapter_version=HISTORICAL_EXPORT_ADAPTER_VERSION,
        tzdata_version="2026.5",
        series_key=series_id,
        representation_record_id=body["representation"]["representation_record_id"],
        input_fingerprint=body["representation"]["provenance"]["input_fingerprint"],
        projection_fingerprint=hashlib.sha256(
            f"projection:{series_id}:{day}".encode()
        ).hexdigest(),
        evidence_content_sha256=capture.content_sha256,
        historical_submission_id=submission.id if submission else None,
        submission_linked_at=datetime.now(UTC) if submission else None,
        submission_body=body,
        audit={"fixture": True},
    )
    db.add(preparation)
    await db.flush()
    return capture, preparation


def service(db):
    return AdultMessageWindowService(AdultMessageWindowRepository(db))


@pytest.mark.asyncio
async def test_requires_existing_profile_and_local_monday(db_session):
    with pytest.raises(AdultMessageWindowProfileNotFoundError):
        await service(db_session).diagnose(
            profile_id=uuid.uuid4(), evaluation_date=EVALUATION_DATE
        )
    profile, _ = await create_profile(db_session)
    await db_session.commit()

    with pytest.raises(AdultMessageWindowDateError):
        await service(db_session).diagnose(
            profile_id=profile.id,
            evaluation_date=EVALUATION_DATE + timedelta(days=1),
        )


@pytest.mark.asyncio
async def test_empty_window_has_exact_dates_and_never_substitutes_zero(db_session):
    profile, _ = await create_profile(db_session)
    await db_session.commit()

    result = await service(db_session).diagnose(
        profile_id=profile.id,
        evaluation_date=EVALUATION_DATE,
    )

    assert len(result.days) == 28
    assert result.window_start == WINDOW_START
    assert result.window_end == date(2026, 2, 1)
    assert result.reference_start == WINDOW_START
    assert result.reference_end == date(2026, 1, 18)
    assert result.recent_start == date(2026, 1, 19)
    assert result.recent_end == date(2026, 2, 1)
    assert [item.period for item in result.days[:14]] == ["REFERENCE"] * 14
    assert [item.period for item in result.days[14:]] == ["RECENT"] * 14
    assert all(item.f12_status == "MISSING" for item in result.days)
    assert all(item.minutes is None for item in result.days)
    assert all(item.quality_verified is False for item in result.days)
    assert result.selected_series is None
    assert result.evaluation_ready is False
    assert {item.code for item in result.blockers} >= {
        "SERIES_NOT_FOUND",
        "F12_DAYS_UNAVAILABLE",
        "QUALITY_POLICY_UNDEFINED",
        "ASSIGNMENT_HISTORY_MISSING",
        "SHARED_PHONE_HISTORY_MISSING",
        "CONTEXT_HISTORY_MISSING",
    }


@pytest.mark.asyncio
async def test_single_series_reports_real_f12_states_corrections_and_declarations(
    db_session,
):
    profile, device = await create_profile(db_session)
    adult = AdultAccount(email=f"weekly-{uuid.uuid4()}@example.com")
    db_session.add(adult)
    await db_session.flush()
    db_session.add(
        AutomaticOfferSettings(
            profile_id=profile.id,
            adult_id=adult.id,
            automatic_enabled=True,
            shared_phone=False,
        )
    )
    db_session.add(
        ProfileContext(
            profile_id=profile.id,
            type=ProfileContextType.ROUTINE_CHANGE,
            start_date=date(2026, 1, 20),
            end_date=date(2026, 1, 22),
            description="fictitious",
        )
    )
    await add_evidence(
        db_session, profile, device, WINDOW_START, minutes=120.0, c1=True
    )
    await add_evidence(
        db_session,
        profile,
        device,
        WINDOW_START + timedelta(days=1),
        status="OBSERVED_ZERO",
        minutes=0.0,
    )
    await add_evidence(
        db_session,
        profile,
        device,
        WINDOW_START + timedelta(days=2),
        status="MISSING",
        minutes=None,
    )
    await add_evidence(
        db_session,
        profile,
        device,
        WINDOW_START + timedelta(days=3),
        status="NOT_APPLICABLE",
        minutes=None,
    )
    await add_evidence(
        db_session,
        profile,
        device,
        WINDOW_START + timedelta(days=4),
        evidence_state="UNAVAILABLE",
        prepared=False,
    )
    await add_evidence(
        db_session,
        profile,
        device,
        WINDOW_START + timedelta(days=5),
        prepared=False,
    )
    correction_day = WINDOW_START + timedelta(days=6)
    await add_evidence(db_session, profile, device, correction_day, minutes=80.0)
    await add_evidence(
        db_session,
        profile,
        device,
        correction_day,
        revision=2,
        prepared=False,
    )
    await db_session.commit()

    result = await service(db_session).diagnose(
        profile_id=profile.id,
        evaluation_date=EVALUATION_DATE,
    )

    assert result.selected_series.series_id == SERIES_A
    assert result.selected_series.device_id == device.id
    assert result.selected_series.time_zone == "UTC"
    assert result.selected_series.measurement_version == HISTORICAL_MEASUREMENT_VERSION
    assert result.days[0].f12_status == "OBSERVED"
    assert result.days[0].minutes == 120.0
    assert result.days[0].c1_evaluation_id == "eval-2026-01-05"
    assert result.days[1].f12_status == "OBSERVED_ZERO"
    assert result.days[1].minutes == 0.0
    assert result.days[2].f12_status == "MISSING"
    assert result.days[2].minutes is None
    assert result.days[2].f12_quality_flags == ["INCOMPLETE_DAILY_CAPTURE"]
    assert "F12_QUALITY_FLAG:INCOMPLETE_DAILY_CAPTURE" in result.days[2].problems
    assert result.days[3].f12_status == "NOT_APPLICABLE"
    assert result.days[4].problems == ["EVIDENCE_UNAVAILABLE"]
    assert result.days[4].unavailable_reason == "NOT_EXPORTED"
    assert result.days[5].problems == ["PREPARATION_MISSING"]
    assert result.days[6].revision == 2
    assert result.days[6].available_revisions == [1, 2]
    assert result.days[6].minutes is None
    assert result.days[6].problems == ["CORRECTION_POLICY_PENDING"]
    assert all(item.quality_verified is False for item in result.days)
    assert result.declarations.assignment.current_state_present is True
    assert result.declarations.assignment.contract_value is None
    assert result.declarations.shared_phone.current_state_present is True
    assert result.declarations.shared_phone.contract_value is None
    assert result.declarations.family_context.historical_records_present is True
    assert result.declarations.family_context.problem == "CONTEXT_MAPPING_UNDEFINED"
    assert result.declarations.context_records[0].type == "ROUTINE_CHANGE"
    assert "F12_CORRECTIONS_PENDING" in {item.code for item in result.blockers}
    assert result.evaluation_ready is False


@pytest.mark.asyncio
async def test_multiple_series_are_reported_without_combining_days(db_session):
    profile, first_device = await create_profile(db_session)
    second_device = Device(
        profile_id=profile.id,
        installation_id=f"weekly-second-{uuid.uuid4()}",
        platform="ANDROID",
    )
    db_session.add(second_device)
    await db_session.flush()
    await add_evidence(
        db_session, profile, first_device, WINDOW_START, series_id=SERIES_A
    )
    await add_evidence(
        db_session,
        profile,
        second_device,
        WINDOW_START + timedelta(days=1),
        series_id=SERIES_B,
    )
    await db_session.commit()

    result = await service(db_session).diagnose(
        profile_id=profile.id,
        evaluation_date=EVALUATION_DATE,
    )

    assert result.selected_series is None
    assert len(result.candidate_series) == 2
    assert result.observed_day_count == 0
    assert result.days[0].f12_status == "MISSING"
    assert result.days[0].evidence_available is True
    assert result.days[0].evidence_id is None
    assert result.days[0].problems == [
        "SERIES_AMBIGUOUS",
        "SERIES_SELECTION_REQUIRED",
    ]
    assert "SERIES_AMBIGUOUS" in {item.code for item in result.blockers}


@pytest.mark.asyncio
async def test_diagnostic_is_read_only_and_creates_no_weekly_job(db_session):
    profile, device = await create_profile(db_session)
    await add_evidence(db_session, profile, device, WINDOW_START)
    await db_session.commit()
    before = await db_session.scalar(select(func.count(AdultMessageEvaluationJob.id)))

    await service(db_session).diagnose(
        profile_id=profile.id,
        evaluation_date=EVALUATION_DATE,
    )

    after = await db_session.scalar(select(func.count(AdultMessageEvaluationJob.id)))
    assert before == after == 0
    assert not db_session.new
    assert not db_session.dirty
