import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.config import Settings
from app.db.models import (
    Device,
    F12EvidencePreparation,
    F12ProcessingJob,
    F12ProcessingJobStatus,
    HistoricalSubmission,
    HistoricalSubmissionStatus,
)
from app.repositories.f12_preparation_repository import F12PreparationRepository
from app.repositories.f12_processing_repository import F12ProcessingRepository
from app.schemas.historical_submission import HistoricalSubmissionCreate
from app.services.f12_automatic_processing import F12AutomaticProcessingService
from app.services.f12_preparation_service import F12PreparationService
from app.services.historical_analytics_client import HistoricalAnalyticsResult
from app.services.historical_submission_service import HistoricalSubmissionService
from test_f12_evidence import (
    authorized_device,
    available_upload,
    endpoint,
    multipart,
    unavailable_upload,
)


def processed_result(request_id: str, *, replayed: bool = False):
    response = {
        "status": "processed",
        "request_id": request_id,
        "replayed": replayed,
        "historical_api_version": "historical-http-v1-candidate",
        "configuration_id": "controlled-test",
        "configuration_fingerprint": "a" * 64,
        "scope": "internal_technical_candidate",
        "result": {
            "reference": {
                "location": None,
                "scale": None,
                "history_count": 0,
                "reference_cutoff": "2026-11-18T00:00:00+00:00",
            },
            "evaluation": {
                "evaluation_id": f"eval-{request_id}",
                "outcome": "ABSTAIN",
                "abstention_reason": "INSUFFICIENT_HISTORY",
            },
            "emission": {"decision": "NO_EMIT", "event": None},
            "next_state": {},
        },
    }
    return HistoricalAnalyticsResult(
        kind="processed",
        response=response,
        replayed=replayed,
        evaluation_id=response["result"]["evaluation"]["evaluation_id"],
    )


class FakeAnalytics:
    def __init__(self, *results):
        self.results = list(results)
        self.payloads = []

    async def evaluate(self, payload):
        self.payloads.append(payload)
        if self.results:
            return self.results.pop(0)
        return processed_result(payload["request_id"])


async def receive_available(client, adult_session, monitoring_authorization):
    profile, device, headers = await authorized_device(
        client, adult_session, monitoring_authorization, "F12 automatic"
    )
    manifest, summary, events = available_upload(profile["id"], device["id"])
    response = await client.post(
        endpoint(profile["id"]),
        headers=headers,
        files=multipart(manifest, summary, events),
    )
    assert response.status_code == 201, response.text
    return profile, device, headers, manifest, summary, events, response


async def claim_and_process(db, analytics, *, worker_id="worker-1", now=None):
    repository = F12ProcessingRepository(db)
    job = await repository.claim_next(
        worker_id=worker_id,
        lease_seconds=60,
        max_attempts=3,
        now=now,
    )
    assert job is not None
    await F12AutomaticProcessingService(
        db,
        analytics,
        retry_seconds=0,
        max_attempts=3,
    ).process(job.id, worker_id=worker_id)
    return await repository.get(job.id)


def test_automatic_processing_feature_is_disabled_by_default():
    assert Settings().f12_automatic_processing_enabled is False


@pytest.mark.asyncio
async def test_receipt_only_enqueues_durable_work_and_identical_retry_is_idempotent(
    client,
    db_session,
    adult_session,
    monitoring_authorization,
):
    profile, _, headers, manifest, summary, events, first = await receive_available(
        client, adult_session, monitoring_authorization
    )
    repeated = await client.post(
        endpoint(profile["id"]),
        headers=headers,
        files=multipart(manifest, summary, events),
    )

    assert first.status_code == 201
    assert repeated.status_code == 200
    assert repeated.json() == first.json()
    raw_job = await db_session.scalar(select(F12ProcessingJob))
    job = await F12ProcessingRepository(db_session).get(raw_job.id)
    assert job.status == F12ProcessingJobStatus.PENDING
    assert job.attempts == 0
    assert await db_session.scalar(select(func.count(F12ProcessingJob.id))) == 1
    assert await db_session.scalar(select(func.count(F12EvidencePreparation.id))) == 0
    assert await db_session.scalar(select(func.count(HistoricalSubmission.id))) == 0


@pytest.mark.asyncio
async def test_available_evidence_is_prepared_submitted_and_abstain_is_preserved(
    client,
    db_session,
    adult_session,
    monitoring_authorization,
):
    await receive_available(client, adult_session, monitoring_authorization)
    analytics = FakeAnalytics()

    job = await claim_and_process(db_session, analytics)

    submission = await db_session.scalar(select(HistoricalSubmission))
    assert job.status == F12ProcessingJobStatus.COMPLETED
    assert submission.status == HistoricalSubmissionStatus.PROCESSED
    assert submission.analytics_response["result"]["evaluation"] == {
        "evaluation_id": f"eval-{submission.request_id}",
        "outcome": "ABSTAIN",
        "abstention_reason": "INSUFFICIENT_HISTORY",
    }
    assert len(analytics.payloads) == 1
    assert await db_session.scalar(select(func.count(F12EvidencePreparation.id))) == 1
    assert await db_session.scalar(select(func.count(HistoricalSubmission.id))) == 1


@pytest.mark.asyncio
async def test_expired_lease_is_recovered_after_restart_without_duplicate_submission(
    client,
    db_session,
    adult_session,
    monitoring_authorization,
):
    await receive_available(client, adult_session, monitoring_authorization)
    repository = F12ProcessingRepository(db_session)
    started = datetime.now(UTC)
    abandoned = await repository.claim_next(
        worker_id="dead-worker",
        lease_seconds=10,
        max_attempts=3,
        now=started,
    )
    assert abandoned.status == F12ProcessingJobStatus.PROCESSING

    analytics = FakeAnalytics()
    recovered = await claim_and_process(
        db_session,
        analytics,
        worker_id="replacement-worker",
        now=started + timedelta(seconds=11),
    )

    assert recovered.id == abandoned.id
    assert recovered.attempts == 2
    assert recovered.status == F12ProcessingJobStatus.COMPLETED
    assert len(analytics.payloads) == 1
    assert await db_session.scalar(select(func.count(HistoricalSubmission.id))) == 1


@pytest.mark.asyncio
async def test_pending_submission_created_before_transport_is_resumed_once(
    client,
    db_session,
    adult_session,
    monitoring_authorization,
):
    _, device_data, _, _, _, _, _ = await receive_available(
        client, adult_session, monitoring_authorization
    )
    raw_job = await db_session.scalar(select(F12ProcessingJob))
    job = await F12ProcessingRepository(db_session).get(raw_job.id)
    evidence = job.evidence
    preparation = await F12PreparationService(
        F12PreparationRepository(db_session)
    ).prepare(
        evidence_id=evidence.id,
        profile_id=evidence.profile_id,
        device_id=evidence.device_id,
    )
    device = await db_session.get(Device, uuid.UUID(device_data["id"]))
    pending = HistoricalSubmissionService._prepare_submission(
        profile_id=evidence.profile_id,
        device_id=device.id,
        data=HistoricalSubmissionCreate.model_validate(preparation.submission_body),
    )
    db_session.add(pending)
    await db_session.commit()
    analytics = FakeAnalytics()

    processed = await claim_and_process(db_session, analytics)

    assert processed.status == F12ProcessingJobStatus.COMPLETED
    assert len(analytics.payloads) == 1
    assert await db_session.scalar(select(func.count(HistoricalSubmission.id))) == 1
    await db_session.refresh(preparation)
    assert preparation.historical_submission_id == pending.id


@pytest.mark.asyncio
async def test_concurrent_workers_can_claim_an_evidence_only_once(
    client,
    db_session_factory,
    adult_session,
    monitoring_authorization,
):
    await receive_available(client, adult_session, monitoring_authorization)
    now = datetime.now(UTC)

    async def claim(worker_id):
        async with db_session_factory() as db:
            return await F12ProcessingRepository(db).claim_next(
                worker_id=worker_id,
                lease_seconds=60,
                max_attempts=3,
                now=now,
            )

    claims = await asyncio.gather(claim("worker-a"), claim("worker-b"))

    assert sum(item is not None for item in claims) == 1


@pytest.mark.asyncio
async def test_retryable_error_reuses_frozen_request_and_does_not_duplicate(
    client,
    db_session,
    adult_session,
    monitoring_authorization,
):
    await receive_available(client, adult_session, monitoring_authorization)
    analytics = FakeAnalytics(
        HistoricalAnalyticsResult.technical_error(
            "HISTORICAL_TIMEOUT",
            "controlled timeout",
            retryable=True,
        )
    )
    first = await claim_and_process(db_session, analytics)
    assert first.status == F12ProcessingJobStatus.TECHNICAL_ERROR
    assert first.retryable is True

    analytics.results.append(
        processed_result(analytics.payloads[0]["request_id"], replayed=True)
    )
    second = await claim_and_process(
        db_session,
        analytics,
        worker_id="worker-2",
        now=datetime.now(UTC) + timedelta(seconds=1),
    )

    submission = await db_session.scalar(select(HistoricalSubmission))
    assert second.status == F12ProcessingJobStatus.COMPLETED
    assert submission.attempts == 2
    assert submission.analytics_replayed is True
    assert analytics.payloads[0] == analytics.payloads[1]
    assert await db_session.scalar(select(func.count(HistoricalSubmission.id))) == 1


@pytest.mark.asyncio
async def test_sending_submission_is_not_blindly_retried_after_restart(
    client,
    db_session,
    adult_session,
    monitoring_authorization,
):
    _, device_data, _, _, _, _, _ = await receive_available(
        client, adult_session, monitoring_authorization
    )
    raw_job = await db_session.scalar(select(F12ProcessingJob))
    job = await F12ProcessingRepository(db_session).get(raw_job.id)
    evidence = job.evidence
    preparation = await F12PreparationService(
        F12PreparationRepository(db_session)
    ).prepare(
        evidence_id=evidence.id,
        profile_id=evidence.profile_id,
        device_id=evidence.device_id,
    )
    device = await db_session.get(Device, uuid.UUID(device_data["id"]))
    pending = HistoricalSubmissionService._prepare_submission(
        profile_id=evidence.profile_id,
        device_id=device.id,
        data=HistoricalSubmissionCreate.model_validate(preparation.submission_body),
    )
    pending.status = HistoricalSubmissionStatus.SENDING
    pending.attempts = 1
    db_session.add(pending)
    await db_session.commit()

    analytics = FakeAnalytics()
    processed = await claim_and_process(db_session, analytics)

    assert processed.status == F12ProcessingJobStatus.MANUAL_REVIEW
    assert processed.last_error_code == "submission_result_uncertain"
    assert analytics.payloads == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["unavailable", "revision"])
async def test_unavailable_and_corrected_evidence_stay_blocked(
    client,
    db_session,
    adult_session,
    monitoring_authorization,
    kind,
):
    profile, device, headers = await authorized_device(
        client, adult_session, monitoring_authorization, f"F12 blocked {kind}"
    )
    if kind == "unavailable":
        manifest = unavailable_upload(profile["id"], device["id"])
        files = multipart(manifest)
        expected = "unavailable_policy_undefined"
    else:
        manifest, summary, events = available_upload(
            profile["id"], device["id"], revision=2
        )
        files = multipart(manifest, summary, events)
        expected = "corrected_revision_policy_undefined"

    response = await client.post(endpoint(profile["id"]), headers=headers, files=files)
    assert response.status_code == 201
    job = await db_session.scalar(select(F12ProcessingJob))
    assert job.status == F12ProcessingJobStatus.BLOCKED
    assert job.last_error_code == expected
    assert await F12ProcessingRepository(db_session).claim_next(
        worker_id="worker",
        lease_seconds=60,
        max_attempts=3,
    ) is None


@pytest.mark.asyncio
async def test_inactive_device_blocks_before_preparation_or_analytics(
    client,
    db_session,
    adult_session,
    monitoring_authorization,
):
    _, device_data, _, _, _, _, _ = await receive_available(
        client, adult_session, monitoring_authorization
    )
    device = await db_session.get(Device, uuid.UUID(device_data["id"]))
    device.active = False
    await db_session.commit()
    analytics = FakeAnalytics()

    job = await claim_and_process(db_session, analytics)

    assert job.status == F12ProcessingJobStatus.BLOCKED
    assert job.last_error_code == "monitoring_authorization_invalid"
    assert analytics.payloads == []
    assert await db_session.scalar(select(func.count(F12EvidencePreparation.id))) == 0
