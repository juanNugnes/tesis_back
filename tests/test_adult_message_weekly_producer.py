import os
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.db.models import (
    AdultAccount,
    AdultMessageEvaluationJob,
    AdultMessageEvaluationJobStatus,
    AdultWeeklyDeclaration,
    Avatar,
    Consent,
    ConsentPurpose,
    ConsentPurposeType,
    ConsentStatus,
    Device,
    Profile,
)
from app.repositories.adult_message_evaluation_repository import (
    AdultMessageEvaluationRepository,
)
from app.repositories.adult_message_window_repository import (
    AdultMessageWindowRepository,
)
from app.repositories.f12_preparation_repository import F12PreparationRepository
from app.services.adult_message_evaluation_service import (
    AdultMessageEvaluationContentConflictError,
    AdultMessageEvaluationService,
)
from app.services.adult_message_weekly_producer import AdultMessageWeeklyProducer
from app.services.adult_message_window_service import AdultMessageWindowService
from app.services.adult_messages_client import AdultMessagesClient
from app.services.f12_evidence_validator import expected_capture_id
from app.services.f12_preparation_service import F12PreparationService
from test_adult_message_evaluations import ControlledAnalytics, make_service
from test_f12_operational_quality import quality_fixture


EVALUATION_DATE = date(2026, 2, 2)
WINDOW_START = EVALUATION_DATE - timedelta(days=28)


async def _profile_device_adult(db):
    profile = Profile(alias="Real verified weekly window", age=9, avatar=Avatar.fox)
    db.add(profile)
    await db.flush()
    device = Device(
        profile_id=profile.id,
        installation_id=f"weekly-real-{uuid.uuid4()}",
        platform="ANDROID",
    )
    adult = AdultAccount(email=f"weekly-real-{uuid.uuid4()}@example.com")
    db.add_all([device, adult])
    await db.flush()
    return profile, device, adult


async def _add_verified_window(db, profile, device, *, recent_minutes=60):
    preparation_service = F12PreparationService(F12PreparationRepository(db))
    preparations = []
    for offset in range(28):
        day = WINDOW_START + timedelta(days=offset)
        capture, _ = quality_fixture(
            day=day,
            observed_minutes=60 if offset < 14 else recent_minutes,
        )
        capture.profile_id = profile.id
        capture.device_id = device.id
        capture.capture_id = expected_capture_id(
            profile.id,
            device.id,
            day,
            capture.revision,
            capture.content_sha256,
        )
        db.add(capture)
        await db.commit()
        preparations.append(
            await preparation_service.prepare(
                evidence_id=capture.id,
                profile_id=profile.id,
                device_id=device.id,
            )
        )
    return preparations


async def _add_historical_prerequisites(db, profile, device, adult, *, revision=1):
    declaration = AdultWeeklyDeclaration(
        profile_id=profile.id,
        device_id=device.id,
        created_by_adult_id=adult.id,
        revision=revision,
        time_zone="UTC",
        effective_from=WINDOW_START,
        effective_to=EVALUATION_DATE - timedelta(days=1),
        assignment_verified=True,
        shared_phone=False,
        context_declared=False,
        created_at=datetime(2026, 1, 4, tzinfo=UTC) + timedelta(minutes=revision),
    )
    db.add(declaration)
    if revision == 1:
        consent = Consent(
            adult_id=adult.id,
            profile_id=profile.id,
            status=ConsentStatus.ACTIVE,
            privacy_notice_version="test-real-weekly",
            granted_at=datetime(2026, 1, 4, tzinfo=UTC),
        )
        consent.purposes = [
            ConsentPurpose(purpose=ConsentPurposeType.USAGE_MONITORING)
        ]
        db.add(consent)
    await db.commit()
    return declaration


def _producer(db, analytics: ControlledAnalytics):
    evaluation_service, client = make_service(db, analytics)
    producer = AdultMessageWeeklyProducer(
        AdultMessageWindowService(AdultMessageWindowRepository(db)),
        evaluation_service,
    )
    return producer, evaluation_service, client


@pytest.mark.asyncio
async def test_verified_f12_window_produces_durable_real_job_and_active_history(
    db_session,
):
    profile, device, adult = await _profile_device_adult(db_session)
    profile_id = profile.id
    device_id = device.id
    preparations = await _add_verified_window(db_session, profile, device)
    await _add_historical_prerequisites(
        db_session, profile, device, adult
    )
    analytics = ControlledAnalytics()
    producer, evaluation_service, analytics_client = _producer(db_session, analytics)

    first = await producer.produce(
        profile_id=profile_id,
        evaluation_date=EVALUATION_DATE,
    )

    assert first.diagnostic.evaluation_ready is True
    assert first.diagnostic.blockers == []
    assert first.job is not None
    assert first.job.source == "REAL_VERIFIED"
    assert first.job.status == AdultMessageEvaluationJobStatus.PENDING
    assert first.job.fixture_id is None
    assert first.job.fixture_sha256 is None
    assert first.job.window_fingerprint is not None
    assert first.job.request_id == f"adult-msg-real-{first.job.window_fingerprint}"
    assert first.job.traceability["days"][0]["preparation_id"] == str(
        preparations[0].id
    )
    assert len(first.job.traceability["days"]) == 28
    assert all(row["quality_verified"] for row in first.job.payload["days"])
    assert first.job.payload["c1_evidence_ids"] == []
    assert analytics.calls == []

    replay = await producer.produce(
        profile_id=profile_id,
        evaluation_date=EVALUATION_DATE,
    )
    assert replay.job.id == first.job.id
    assert replay.job.request_id == first.job.request_id
    assert await db_session.scalar(
        select(func.count(AdultMessageEvaluationJob.id))
    ) == 1

    processed = await evaluation_service.execute(first.job.id)
    assert processed.status == AdultMessageEvaluationJobStatus.PROCESSED
    assert processed.analytics_response["status"] == "ACTIVE"
    assert processed.analytics_response["action"] == "CREATE"
    assert len(analytics.calls) == 1
    history = await analytics_client.history(profile_id)
    assert len(history.versions) == 1
    assert history.versions[0].card.status == "ACTIVE"

    correction, _ = quality_fixture(
        day=WINDOW_START + timedelta(days=10), revision=2
    )
    correction.profile_id = profile_id
    correction.device_id = device_id
    correction.capture_id = expected_capture_id(
        profile_id,
        device_id,
        correction.local_date,
        correction.revision,
        correction.content_sha256,
    )
    db_session.add(correction)
    await db_session.commit()
    blocked_correction = await producer.produce(
        profile_id=profile_id,
        evaluation_date=EVALUATION_DATE,
    )
    assert blocked_correction.job is None
    assert "F12_CORRECTIONS_PENDING" in {
        blocker.code for blocker in blocked_correction.diagnostic.blockers
    }
    assert len(analytics.calls) == 1
    assert await db_session.scalar(
        select(func.count(AdultMessageEvaluationJob.id))
    ) == 1


@pytest.mark.asyncio
async def test_blocked_window_returns_diagnostic_without_creating_job(db_session):
    profile, _, _ = await _profile_device_adult(db_session)
    await db_session.commit()
    analytics = ControlledAnalytics()
    producer, _, _ = _producer(db_session, analytics)

    result = await producer.produce(
        profile_id=profile.id,
        evaluation_date=EVALUATION_DATE,
    )

    assert result.blocked is True
    assert result.job is None
    assert result.diagnostic.evaluation_ready is False
    assert "F12_DAYS_UNAVAILABLE" in {
        blocker.code for blocker in result.diagnostic.blockers
    }
    assert await db_session.scalar(
        select(func.count(AdultMessageEvaluationJob.id))
    ) == 0
    assert analytics.calls == []


@pytest.mark.asyncio
async def test_new_declaration_cannot_silently_reevaluate_processed_window(db_session):
    profile, device, adult = await _profile_device_adult(db_session)
    await _add_verified_window(db_session, profile, device)
    await _add_historical_prerequisites(db_session, profile, device, adult)
    analytics = ControlledAnalytics()
    producer, evaluation_service, _ = _producer(db_session, analytics)
    original = await producer.produce(
        profile_id=profile.id,
        evaluation_date=EVALUATION_DATE,
    )
    await evaluation_service.execute(original.job.id)

    await _add_historical_prerequisites(
        db_session,
        profile,
        device,
        adult,
        revision=2,
    )
    with pytest.raises(AdultMessageEvaluationContentConflictError):
        await producer.produce(
            profile_id=profile.id,
            evaluation_date=EVALUATION_DATE,
        )

    assert await db_session.scalar(
        select(func.count(AdultMessageEvaluationJob.id))
    ) == 1
    assert len(analytics.calls) == 1


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("RUN_REAL_ADULT_MESSAGES_ANALYTICS_TESTS") != "1",
    reason="requires an explicitly isolated local Analytics instance",
)
async def test_verified_window_against_isolated_local_analytics(db_session):
    profile, device, adult = await _profile_device_adult(db_session)
    await _add_verified_window(
        db_session,
        profile,
        device,
        recent_minutes=120,
    )
    await _add_historical_prerequisites(db_session, profile, device, adult)
    client = AdultMessagesClient(
        os.environ["REAL_ADULT_MESSAGES_ANALYTICS_URL"],
        os.environ["REAL_ADULT_MESSAGES_ANALYTICS_TOKEN"],
        5.0,
    )
    evaluation_service = AdultMessageEvaluationService(
        AdultMessageEvaluationRepository(db_session), client
    )
    producer = AdultMessageWeeklyProducer(
        AdultMessageWindowService(AdultMessageWindowRepository(db_session)),
        evaluation_service,
    )

    produced = await producer.produce(
        profile_id=profile.id,
        evaluation_date=EVALUATION_DATE,
    )
    processed = await evaluation_service.execute(produced.job.id)

    assert processed.status == AdultMessageEvaluationJobStatus.PROCESSED
    assert processed.analytics_response["status"] == "ACTIVE"
    assert processed.analytics_response["action"] == "CREATE"
    history = await client.history(profile.id)
    assert len(history.versions) == 1
    assert history.versions[0].card.status == "ACTIVE"
