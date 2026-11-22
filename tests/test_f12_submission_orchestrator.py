import asyncio
import hashlib
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.db.models import (
    Device,
    F12EvidencePreparation,
    F12RawEvidenceCapture,
    F12RawEvidencePair,
    HistoricalSubmission,
    HistoricalSubmissionStatus,
)
from app.repositories.f12_preparation_repository import F12PreparationRepository
from app.repositories.historical_submission_repository import (
    HistoricalSubmissionRepository,
)
from app.schemas.f12_evidence import F12_EXPORT_ADAPTER_VERSION
from app.schemas.historical_submission import HistoricalSubmissionCreate
from app.services.f12_preparation_service import (
    F12PreparationIneligibleError,
    F12PreparationService,
)
from app.services.f12_submission_orchestrator import (
    F12SubmissionAuthorizationError,
    F12SubmissionOrchestrator,
    F12SubmissionPersistenceError,
)
from app.services.historical_analytics_client import HistoricalAnalyticsResult
from app.services.historical_submission_service import (
    HistoricalSubmissionContentConflictError,
    HistoricalSubmissionService,
)
from app.services.monitoring_authorization_service import (
    MonitoringAuthorizationService,
)


FIXTURES = Path(__file__).parent / "fixtures" / "f12_contract_20261121"


def _processed_result(request_id: str, *, replayed: bool = False):
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
                "reference_cutoff": "2026-09-01T03:00:00+00:00",
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


class FakeHistoricalClient:
    def __init__(self, *results):
        self.results = list(results)
        self.payloads = []

    async def evaluate(self, payload):
        self.payloads.append(payload)
        if self.results:
            return self.results.pop(0)
        return _processed_result(payload["request_id"])


class BlockingHistoricalClient(FakeHistoricalClient):
    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def evaluate(self, payload):
        self.payloads.append(payload)
        self.started.set()
        await self.release.wait()
        return _processed_result(payload["request_id"])


class FailOnceLinkRepository(F12PreparationRepository):
    def __init__(self, db):
        super().__init__(db)
        self.failed = False

    async def link_submission(self, **kwargs):
        if not self.failed:
            self.failed = True
            raise SQLAlchemyError("controlled link failure")
        return await super().link_submission(**kwargs)


async def _create_profile_device_and_authorization(
    client,
    adult_session,
    monitoring_authorization,
):
    created = await client.post(
        "/api/v1/profiles",
        json={"alias": "F12 C1 fictitious", "age": 9, "avatar": "fox"},
    )
    assert created.status_code == 201
    profile = created.json()
    registered = await client.post(
        f"/api/v1/profiles/{profile['id']}/devices",
        headers={"Authorization": f"Bearer {profile['bootstrap_token']}"},
        json={
            "installation_id": f"f12-c1-{uuid.uuid4()}",
            "platform": "ANDROID",
        },
    )
    assert registered.status_code == 201
    adult_headers, adult = await adult_session(profile["id"])
    await monitoring_authorization(profile["id"], adult=adult)
    return profile, registered.json(), adult_headers


async def _persist_and_prepare(
    client,
    db_session,
    adult_session,
    monitoring_authorization,
):
    profile, device, adult_headers = await _create_profile_device_and_authorization(
        client,
        adult_session,
        monitoring_authorization,
    )
    summary_bytes = (FIXTURES / "originals" / "summary.json").read_bytes()
    events_bytes = (FIXTURES / "originals" / "events.jsonl").read_bytes()
    summary = json.loads(summary_bytes)
    content_sha256 = hashlib.sha256(
        summary_bytes + b"\0" + events_bytes + b"\0"
    ).hexdigest()
    capture = F12RawEvidenceCapture(
        capture_id=hashlib.sha256(
            f"{profile['id']}:{device['id']}:{content_sha256}".encode()
        ).hexdigest(),
        profile_id=uuid.UUID(profile["id"]),
        device_id=uuid.UUID(device["id"]),
        local_date=date(2026, 9, 1),
        time_zone="America/Argentina/Buenos_Aires",
        revision=1,
        envelope_version=1,
        adapter_version=F12_EXPORT_ADAPTER_VERSION,
        evidence_state="AVAILABLE",
        unavailable_reason=None,
        content_sha256=content_sha256,
        pair_count=1,
        total_bytes=len(summary_bytes) + len(events_bytes),
        source_created_at=datetime(2026, 9, 2, 3, 2, tzinfo=UTC),
        receipt_status="RECEIVED",
        received_at=datetime(2026, 9, 2, 3, 3, tzinfo=UTC),
    )
    capture.pairs = [
        F12RawEvidencePair(
            ordinal=0,
            collector_run_id=summary["collectorRunId"],
            summary_sha256=hashlib.sha256(summary_bytes).hexdigest(),
            events_sha256=hashlib.sha256(events_bytes).hexdigest(),
            summary_size=len(summary_bytes),
            events_size=len(events_bytes),
            summary_bytes=summary_bytes,
            events_bytes=events_bytes,
        )
    ]
    db_session.add(capture)
    await db_session.commit()
    preparation = await F12PreparationService(
        F12PreparationRepository(db_session)
    ).prepare(
        evidence_id=capture.id,
        profile_id=capture.profile_id,
        device_id=capture.device_id,
    )
    return profile, device, adult_headers, capture, preparation


def _orchestrator(db, fake_client, *, repository=None):
    historical = HistoricalSubmissionService(
        HistoricalSubmissionRepository(db),
        fake_client,
    )
    return F12SubmissionOrchestrator(
        repository or F12PreparationRepository(db),
        historical,
        MonitoringAuthorizationService(db),
    )


@pytest.mark.asyncio
async def test_valid_preparation_evaluates_c1_and_adult_get_exposes_abstain(
    client,
    db_session,
    adult_session,
    monitoring_authorization,
):
    profile, _, adult_headers, _, preparation = await _persist_and_prepare(
        client, db_session, adult_session, monitoring_authorization
    )
    analytics = FakeHistoricalClient()

    submission = await _orchestrator(db_session, analytics).submit(
        preparation_id=preparation.id
    )

    assert submission.status == HistoricalSubmissionStatus.PROCESSED
    assert submission.analytics_response["result"]["evaluation"]["outcome"] == "ABSTAIN"
    assert len(analytics.payloads) == 1
    sent = analytics.payloads[0]
    prepared_representation = preparation.submission_body["representation"]
    assert sent["request_id"] == preparation.request_id
    assert sent["representation"]["subject_id"] == prepared_representation["subject_id"]
    assert sent["representation"]["representation_record_id"] == (
        prepared_representation["representation_record_id"]
    )
    assert sent["representation"]["provenance"] == (
        prepared_representation["provenance"]
    )
    normalized = HistoricalSubmissionCreate.model_validate(
        {
            **preparation.submission_body,
            "representation": sent["representation"],
        }
    )
    original = HistoricalSubmissionCreate.model_validate(preparation.submission_body)
    assert normalized == original
    await db_session.refresh(preparation)
    assert preparation.historical_submission_id == submission.id

    response = await client.get(
        f"/api/v1/profiles/{profile['id']}/historical-submissions",
        headers=adult_headers,
    )
    assert response.status_code == 200
    assert response.json()[0]["analytics_evaluation"] == {
        "outcome": "ABSTAIN",
        "abstention_reason": "INSUFFICIENT_HISTORY",
    }


@pytest.mark.asyncio
async def test_identical_submit_reconciles_without_duplicate_evaluation(
    client,
    db_session,
    adult_session,
    monitoring_authorization,
):
    _, _, _, _, preparation = await _persist_and_prepare(
        client, db_session, adult_session, monitoring_authorization
    )
    analytics = FakeHistoricalClient()
    orchestrator = _orchestrator(db_session, analytics)

    first = await orchestrator.submit(preparation_id=preparation.id)
    second = await orchestrator.submit(preparation_id=preparation.id)

    assert second.id == first.id
    assert len(analytics.payloads) == 1
    assert await db_session.scalar(select(func.count(HistoricalSubmission.id))) == 1


@pytest.mark.asyncio
async def test_lost_response_retries_same_request_without_duplicate_row(
    client,
    db_session,
    adult_session,
    monitoring_authorization,
):
    _, _, _, _, preparation = await _persist_and_prepare(
        client, db_session, adult_session, monitoring_authorization
    )
    analytics = FakeHistoricalClient(
        HistoricalAnalyticsResult.technical_error(
            "HISTORICAL_TIMEOUT",
            "controlled lost response",
            retryable=True,
        ),
        _processed_result(preparation.request_id, replayed=True),
    )
    orchestrator = _orchestrator(db_session, analytics)

    first = await orchestrator.submit(preparation_id=preparation.id)
    retried = await orchestrator.retry(preparation_id=preparation.id)

    assert first.id == retried.id
    assert first.request_id == retried.request_id == preparation.request_id
    assert retried.status == HistoricalSubmissionStatus.PROCESSED
    assert retried.attempts == 2
    assert retried.analytics_replayed is True
    assert analytics.payloads[0] == analytics.payloads[1]
    assert await db_session.scalar(select(func.count(HistoricalSubmission.id))) == 1


@pytest.mark.asyncio
async def test_link_failure_is_reconciled_by_request_id_without_resending(
    client,
    db_session,
    adult_session,
    monitoring_authorization,
):
    _, _, _, _, preparation = await _persist_and_prepare(
        client, db_session, adult_session, monitoring_authorization
    )
    analytics = FakeHistoricalClient()
    failing_repository = FailOnceLinkRepository(db_session)
    request_id = preparation.request_id
    preparation_id = preparation.id

    with pytest.raises(F12SubmissionPersistenceError):
        await _orchestrator(
            db_session,
            analytics,
            repository=failing_repository,
        ).submit(preparation_id=preparation_id)

    stored = await HistoricalSubmissionRepository(db_session).get_by_request_id(
        request_id
    )
    assert stored is not None and stored.status == HistoricalSubmissionStatus.PROCESSED
    preparation = await db_session.get(F12EvidencePreparation, preparation_id)
    await db_session.refresh(preparation)
    assert preparation.historical_submission_id is None

    reconciled = await _orchestrator(db_session, analytics).submit(
        preparation_id=preparation_id
    )
    assert reconciled.id == stored.id
    assert len(analytics.payloads) == 1
    await db_session.refresh(preparation)
    assert preparation.historical_submission_id == stored.id


@pytest.mark.asyncio
async def test_conflicting_submission_for_same_series_day_is_not_sent_again(
    client,
    db_session,
    adult_session,
    monitoring_authorization,
):
    _, device_data, _, _, preparation = await _persist_and_prepare(
        client, db_session, adult_session, monitoring_authorization
    )
    analytics = FakeHistoricalClient()
    historical = HistoricalSubmissionService(
        HistoricalSubmissionRepository(db_session), analytics
    )
    device = await db_session.get(Device, uuid.UUID(device_data["id"]))
    conflicting_body = dict(preparation.submission_body)
    conflicting_body["request_id"] = "controlled-existing-series-day"
    await historical.create(
        profile_id=device.profile_id,
        device=device,
        data=HistoricalSubmissionCreate.model_validate(conflicting_body),
    )

    with pytest.raises(HistoricalSubmissionContentConflictError):
        await _orchestrator(db_session, analytics).submit(
            preparation_id=preparation.id
        )
    assert len(analytics.payloads) == 1
    assert await db_session.scalar(select(func.count(HistoricalSubmission.id))) == 1


@pytest.mark.asyncio
async def test_concurrent_submit_creates_one_submission_and_one_evaluation(
    client,
    db_session,
    db_session_factory,
    adult_session,
    monitoring_authorization,
):
    _, _, _, _, preparation = await _persist_and_prepare(
        client, db_session, adult_session, monitoring_authorization
    )
    analytics = BlockingHistoricalClient()
    preparation_id = preparation.id

    async with db_session_factory() as first_db, db_session_factory() as second_db:
        first_task = asyncio.create_task(
            _orchestrator(first_db, analytics).submit(
                preparation_id=preparation_id
            )
        )
        await asyncio.wait_for(analytics.started.wait(), timeout=5)
        second_task = asyncio.create_task(
            _orchestrator(second_db, analytics).submit(
                preparation_id=preparation_id
            )
        )
        await asyncio.sleep(0)
        analytics.release.set()
        first, second = await asyncio.gather(first_task, second_task)

    assert first.id == second.id
    assert len(analytics.payloads) == 1
    assert await db_session.scalar(select(func.count(HistoricalSubmission.id))) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("revision", "corrected_revision_policy_undefined"),
        ("unavailable", "unavailable_policy_undefined"),
        ("expired", "evidence_expired"),
    ],
)
async def test_ineligible_evidence_is_never_sent(
    client,
    db_session,
    adult_session,
    monitoring_authorization,
    mutation,
    expected_code,
):
    _, _, _, capture, preparation = await _persist_and_prepare(
        client, db_session, adult_session, monitoring_authorization
    )
    if mutation == "revision":
        capture.revision = 2
    elif mutation == "expired":
        capture.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    else:
        capture.pairs.clear()
        capture.evidence_state = "UNAVAILABLE"
        capture.unavailable_reason = "NOT_EXPORTED"
        capture.pair_count = 0
        capture.total_bytes = 0
    await db_session.commit()
    analytics = FakeHistoricalClient()

    with pytest.raises(F12PreparationIneligibleError) as exc:
        await _orchestrator(db_session, analytics).submit(
            preparation_id=preparation.id
        )
    assert exc.value.code == expected_code
    assert analytics.payloads == []
    assert await db_session.scalar(select(func.count(HistoricalSubmission.id))) == 0


@pytest.mark.asyncio
async def test_inactive_device_is_not_submitted(
    client,
    db_session,
    adult_session,
    monitoring_authorization,
):
    _, device_data, _, _, preparation = await _persist_and_prepare(
        client, db_session, adult_session, monitoring_authorization
    )
    device = await db_session.get(Device, uuid.UUID(device_data["id"]))
    device.active = False
    await db_session.commit()
    analytics = FakeHistoricalClient()

    with pytest.raises(F12SubmissionAuthorizationError):
        await _orchestrator(db_session, analytics).submit(
            preparation_id=preparation.id
        )
    assert analytics.payloads == []


def test_docker_context_explicitly_includes_pinned_f12_sources():
    root = Path(__file__).parents[1]
    dockerignore = (root / ".dockerignore").read_text(encoding="utf-8")
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    assert "!vendor/" in dockerignore
    assert "!vendor/**" in dockerignore
    assert "COPY . ." in dockerfile
    assert (
        root
        / "vendor"
        / "modera_f12_94ff3a8"
        / "analytics_service"
        / "f12_export.py"
    ).is_file()
