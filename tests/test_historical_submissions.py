import asyncio
import json
import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import func, select

from app.api.routes.profile_historical_submissions import (
    get_historical_analytics_client,
)
from app.db.models import HistoricalSubmission, HistoricalSubmissionStatus
from app.main import app
from app.services.historical_analytics_client import (
    HistoricalAnalyticsClient,
    HistoricalAnalyticsResult,
)
from app.services.representative_verification_service import (
    RepresentativeVerificationService,
)


def processed_response(
    request_id,
    *,
    replayed=False,
    outcome="ABSTAIN",
    abstention_reason=None,
):
    if outcome == "ABSTAIN" and abstention_reason is None:
        abstention_reason = "INSUFFICIENT_HISTORY"
    return {
        "status": "processed",
        "request_id": request_id,
        "replayed": replayed,
        "historical_api_version": "historical-http-v1-candidate",
        "configuration_id": "smoke-only-not-calibrated",
        "configuration_fingerprint": "a" * 64,
        "scope": "internal_technical_candidate",
        "result": {
            "reference": {},
            "evaluation": {
                "evaluation_id": f"eval-{request_id}",
                "outcome": outcome,
                "abstention_reason": abstention_reason,
            },
            "emission": {"decision": "NO_EMIT", "event": None},
            "next_state": {},
        },
    }


def processed_result(
    request_id,
    *,
    replayed=False,
    outcome="ABSTAIN",
    abstention_reason=None,
):
    payload = processed_response(
        request_id,
        replayed=replayed,
        outcome=outcome,
        abstention_reason=abstention_reason,
    )
    return HistoricalAnalyticsResult(
        kind="processed",
        response=payload,
        replayed=replayed,
        evaluation_id=payload["result"]["evaluation"]["evaluation_id"],
    )


def technical_result(code="HISTORICAL_TIMEOUT", *, retryable=True):
    return HistoricalAnalyticsResult.technical_error(
        code,
        "Technical historical failure",
        retryable=retryable,
    )


class FakeHistoricalAnalyticsClient:
    def __init__(self, *results):
        self.results = list(results)
        self.payloads = []
        self.before_call = None

    async def evaluate(self, payload):
        self.payloads.append(payload)
        if self.before_call is not None:
            await self.before_call(payload)
        if self.results:
            return self.results.pop(0)
        return processed_result(payload["request_id"])


@pytest.fixture
def historical_client(client):
    fake = FakeHistoricalAnalyticsClient()
    app.dependency_overrides[get_historical_analytics_client] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_historical_analytics_client, None)


async def create_profile_and_device(client, alias):
    created = await client.post(
        "/api/v1/profiles",
        json={"alias": alias, "age": 9, "avatar": "fox"},
    )
    assert created.status_code == 201
    profile = created.json()
    registered = await client.post(
        f"/api/v1/profiles/{profile['id']}/devices",
        headers={"Authorization": f"Bearer {profile['bootstrap_token']}"},
        json={
            "installation_id": f"historical-{uuid.uuid4()}",
            "platform": "ANDROID",
        },
    )
    assert registered.status_code == 201
    device = registered.json()
    return profile, device, {"Authorization": f"Bearer {device['access_token']}"}


def historical_payload(day=date(2026, 9, 1), *, request_id=None, value=60.0):
    zone = ZoneInfo("America/Argentina/Buenos_Aires")
    start = datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC)
    return {
        "request_id": request_id or f"fixture-{day.isoformat()}-{uuid.uuid4().hex[:8]}",
        "time_zone": "America/Argentina/Buenos_Aires",
        "measurement_version": "capture-test-v1",
        "representation": {
            "representation_record_id": "rep-caller-value-is-not-authoritative",
            "representation_spec_id": "daily_use_duration_minutes_v2",
            "subject_id": "series-caller-value-is-not-authoritative",
            "phenomenon": "DAILY_USE_DURATION",
            "interval_start": start.isoformat(),
            "interval_end": end.isoformat(),
            "value": value,
            "unit": "minutes",
            "status": "OBSERVED" if value else "OBSERVED_ZERO",
            "coverage": {
                "value": None,
                "basis": "daily_capture_completeness_not_quantified",
            },
            "quality_flags": [],
            "provenance": {
                "source_id": "android-usage-events",
                "source_version": "f12-backend-bridge-v1-candidate",
                "source_semantics": (
                    "Interactive screen duration on assigned device; "
                    "user attribution unverified"
                ),
                "adapter_version": "v2",
                "input_fingerprint": "fixture-input-fingerprint",
            },
            "computed_at": (end + timedelta(minutes=2)).isoformat(),
        },
    }


async def post_submission(client, profile_id, headers, payload):
    return await client.post(
        f"/api/v1/profiles/{profile_id}/historical-submissions",
        headers=headers,
        json=payload,
    )


async def get_submissions(client, profile_id, headers, params=None):
    return await client.get(
        f"/api/v1/profiles/{profile_id}/historical-submissions",
        headers=headers,
        params=params,
    )


async def verified_adult_session(adult_session, db_session, profile_id):
    headers, adult = await adult_session(profile_id)
    await RepresentativeVerificationService(db_session).approve(
        adult.id,
        uuid.UUID(str(profile_id)),
        verified_by="pytest-historical-read",
    )
    return headers, adult


@pytest.mark.asyncio
async def test_authorized_adult_lists_historical_submission_summaries(
    client,
    adult_session,
    db_session,
    historical_client,
):
    profile, _, device_headers = await create_profile_and_device(client, "History read")
    payload = historical_payload(date(2026, 9, 1))
    created = await post_submission(client, profile["id"], device_headers, payload)
    assert created.status_code == 200
    adult_headers, _ = await verified_adult_session(
        adult_session, db_session, profile["id"]
    )

    response = await get_submissions(client, profile["id"], adult_headers)

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": created.json()["id"],
            "local_date": "2026-09-01",
            "status": "PROCESSED",
            "attempts": 1,
            "analytics_evaluation_id": f"eval-{payload['request_id']}",
            "analytics_evaluation": {
                "outcome": "ABSTAIN",
                "abstention_reason": "INSUFFICIENT_HISTORY",
            },
            "retryable": False,
            "last_error_code": None,
            "last_error_message": None,
        }
    ]
    assert len(historical_client.payloads) == 1
    assert response.headers["cache-control"] == "no-store"
    for private_field in (
        "payload",
        "analytics_response",
        "series_key",
        "device_id",
        "assignment_id",
    ):
        assert private_field not in response.json()[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["NO_CHANGE", "CHANGE"])
async def test_historical_submission_list_preserves_evaluated_c1_outcome(
    client,
    adult_session,
    db_session,
    historical_client,
    outcome,
):
    profile, _, device_headers = await create_profile_and_device(
        client, f"History {outcome}"
    )
    payload = historical_payload()
    historical_client.results = [
        processed_result(payload["request_id"], outcome=outcome)
    ]
    created = await post_submission(client, profile["id"], device_headers, payload)
    assert created.status_code == 200
    adult_headers, _ = await verified_adult_session(
        adult_session, db_session, profile["id"]
    )

    response = await get_submissions(client, profile["id"], adult_headers)

    assert response.status_code == 200
    assert response.json()[0]["status"] == "PROCESSED"
    assert response.json()[0]["analytics_evaluation"] == {
        "outcome": outcome,
        "abstention_reason": None,
    }


@pytest.mark.asyncio
async def test_historical_submission_list_supports_date_and_status_filters(
    client,
    adult_session,
    db_session,
    historical_client,
):
    profile, _, device_headers = await create_profile_and_device(client, "History filters")
    first_payload = historical_payload(date(2026, 9, 1))
    second_payload = historical_payload(date(2026, 9, 2))
    historical_client.results = [
        processed_result(first_payload["request_id"]),
        technical_result(),
    ]
    first = await post_submission(client, profile["id"], device_headers, first_payload)
    second = await post_submission(client, profile["id"], device_headers, second_payload)
    assert first.status_code == 200
    assert second.status_code == 503
    adult_headers, _ = await verified_adult_session(
        adult_session, db_session, profile["id"]
    )

    by_date = await get_submissions(
        client,
        profile["id"],
        adult_headers,
        {"date_from": "2026-09-02", "date_to": "2026-09-02"},
    )
    processed = await get_submissions(
        client,
        profile["id"],
        adult_headers,
        {"status": "PROCESSED"},
    )

    assert by_date.status_code == 200
    assert [item["id"] for item in by_date.json()] == [second.json()["id"]]
    assert by_date.json()[0]["last_error_code"] == "HISTORICAL_TIMEOUT"
    assert by_date.json()[0]["last_error_message"] == "Technical historical failure"
    assert by_date.json()[0]["retryable"] is True
    assert by_date.json()[0]["analytics_evaluation"] is None
    assert processed.status_code == 200
    assert [item["id"] for item in processed.json()] == [first.json()["id"]]

    invalid_range = await get_submissions(
        client,
        profile["id"],
        adult_headers,
        {"date_from": "2026-09-02", "date_to": "2026-09-01"},
    )
    assert invalid_range.status_code == 422


@pytest.mark.asyncio
async def test_historical_submission_list_keeps_missing_evaluation_distinct(
    client,
    adult_session,
    db_session,
    historical_client,
):
    profile, _, device_headers = await create_profile_and_device(
        client, "History pending"
    )
    historical_client.results = [technical_result()]
    created = await post_submission(
        client,
        profile["id"],
        device_headers,
        historical_payload(),
    )
    assert created.status_code == 503
    submission = await db_session.get(
        HistoricalSubmission,
        uuid.UUID(created.json()["id"]),
    )
    submission.status = HistoricalSubmissionStatus.PENDING
    submission.analytics_response = None
    submission.retryable = False
    submission.last_error_code = None
    submission.last_error_message = None
    await db_session.commit()
    adult_headers, _ = await verified_adult_session(
        adult_session, db_session, profile["id"]
    )

    response = await get_submissions(client, profile["id"], adult_headers)

    assert response.status_code == 200
    assert response.json()[0]["status"] == "PENDING"
    assert response.json()[0]["analytics_evaluation"] is None
    assert response.json()[0]["last_error_code"] is None
    assert response.json()[0]["last_error_message"] is None


@pytest.mark.asyncio
async def test_historical_submission_list_requires_verified_adult_access(
    client,
    adult_session,
    historical_client,
):
    profile, _, _ = await create_profile_and_device(client, "History unverified")
    linked_headers, _ = await adult_session(profile["id"])

    response = await get_submissions(client, profile["id"], linked_headers)

    assert response.status_code == 403
    assert response.json()["detail"] == "Verified profile access required"
    assert historical_client.payloads == []


@pytest.mark.asyncio
async def test_historical_submission_list_enforces_adult_profile_access(
    client,
    adult_session,
    db_session,
    historical_client,
):
    profile, _, device_headers = await create_profile_and_device(client, "History owner")
    foreign, _, foreign_device_headers = await create_profile_and_device(
        client, "History foreign"
    )
    created = await post_submission(
        client,
        profile["id"],
        device_headers,
        historical_payload(),
    )
    assert created.status_code == 200
    foreign_created = await post_submission(
        client,
        foreign["id"],
        foreign_device_headers,
        historical_payload(request_id=f"foreign-{uuid.uuid4().hex}"),
    )
    assert foreign_created.status_code == 200
    adult_headers, _ = await verified_adult_session(
        adult_session, db_session, foreign["id"]
    )

    unauthenticated = await get_submissions(client, profile["id"], {})
    unauthorized = await get_submissions(client, profile["id"], adult_headers)
    device_only = await get_submissions(client, profile["id"], device_headers)

    assert unauthenticated.status_code == 401
    assert unauthorized.status_code == 403
    assert device_only.status_code == 401

    own = await get_submissions(client, foreign["id"], adult_headers)
    assert own.status_code == 200
    assert [item["id"] for item in own.json()] == [foreign_created.json()["id"]]


@pytest.mark.asyncio
async def test_historical_submission_list_returns_empty_for_authorized_profile(
    client,
    adult_session,
    db_session,
    historical_client,
):
    profile, _, _ = await create_profile_and_device(client, "History empty")
    adult_headers, _ = await verified_adult_session(
        adult_session, db_session, profile["id"]
    )

    response = await get_submissions(client, profile["id"], adult_headers)

    assert response.status_code == 200
    assert response.json() == []
    assert historical_client.payloads == []


@pytest.mark.asyncio
async def test_device_can_create_submission_for_own_profile(
    client,
    db_session,
    historical_client,
):
    profile, device, headers = await create_profile_and_device(client, "Historical own")
    payload = historical_payload()

    response = await post_submission(client, profile["id"], headers, payload)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "PROCESSED"
    assert body["profile_id"] == profile["id"]
    assert body["device_id"] == device["id"]
    assert body["assignment_id"] == device["id"]
    assert body["attempts"] == 1
    assert await db_session.scalar(
        select(func.count()).select_from(HistoricalSubmission)
    ) == 1


@pytest.mark.asyncio
async def test_other_device_cannot_create_submission_for_profile(
    client,
    historical_client,
):
    profile, _, _ = await create_profile_and_device(client, "Historical owner")
    _, _, foreign_headers = await create_profile_and_device(client, "Historical foreign")

    response = await post_submission(
        client,
        profile["id"],
        foreign_headers,
        historical_payload(),
    )

    assert response.status_code == 403
    assert historical_client.payloads == []


@pytest.mark.asyncio
async def test_authoritative_identity_cannot_be_spoofed(
    client,
    db_session,
    historical_client,
):
    profile, device, headers = await create_profile_and_device(client, "Identity")
    payload = historical_payload()
    payload["representation"]["subject_id"] = "spoofed-subject"
    payload["representation"]["representation_record_id"] = "spoofed-record"

    response = await post_submission(client, profile["id"], headers, payload)

    assert response.status_code == 200
    stored = await db_session.scalar(select(HistoricalSubmission))
    assert stored is not None
    assert stored.profile_id == uuid.UUID(profile["id"])
    assert stored.device_id == stored.assignment_id == uuid.UUID(device["id"])
    assert stored.payload["representation"]["subject_id"] == stored.series_key
    assert stored.payload["representation"]["subject_id"] != "spoofed-subject"
    assert stored.payload["representation"]["representation_record_id"] != "spoofed-record"

    for field in ("profile_id", "device_id", "assignment_id"):
        extra_identity = await post_submission(
            client,
            profile["id"],
            headers,
            {**historical_payload(), field: str(uuid.uuid4())},
        )
        assert extra_identity.status_code == 422


@pytest.mark.asyncio
async def test_payload_is_durable_before_http(
    client,
    db_session_factory,
    historical_client,
):
    profile, _, headers = await create_profile_and_device(client, "Durable")
    observed = {}

    async def assert_persisted(payload):
        async with db_session_factory() as session:
            stored = await session.scalar(
                select(HistoricalSubmission).where(
                    HistoricalSubmission.request_id == payload["request_id"]
                )
            )
            observed["stored"] = stored is not None
            observed["payload"] = stored.payload if stored is not None else None
            observed["status"] = stored.status if stored is not None else None

    historical_client.before_call = assert_persisted
    payload = historical_payload()

    response = await post_submission(client, profile["id"], headers, payload)

    assert response.status_code == 200
    assert observed == {
        "stored": True,
        "payload": historical_client.payloads[0],
        "status": HistoricalSubmissionStatus.SENDING,
    }


@pytest.mark.asyncio
async def test_processed_abstain_is_success_and_replayed_false_is_saved(
    client,
    db_session,
):
    profile, _, headers = await create_profile_and_device(client, "Abstain")
    payload = historical_payload()
    fake = FakeHistoricalAnalyticsClient(
        processed_result(payload["request_id"], replayed=False, outcome="ABSTAIN")
    )
    app.dependency_overrides[get_historical_analytics_client] = lambda: fake
    try:
        response = await post_submission(client, profile["id"], headers, payload)
    finally:
        app.dependency_overrides.pop(get_historical_analytics_client, None)

    assert response.status_code == 200
    assert response.json()["status"] == "PROCESSED"
    assert response.json()["analytics_replayed"] is False
    stored = await db_session.scalar(select(HistoricalSubmission))
    assert stored.analytics_response["result"]["evaluation"]["outcome"] == "ABSTAIN"
    assert stored.analytics_evaluation_id == f"eval-{payload['request_id']}"


@pytest.mark.asyncio
async def test_retry_reuses_request_id_payload_and_saves_replayed_true(
    client,
    db_session,
):
    profile, _, headers = await create_profile_and_device(client, "Retry")
    payload = historical_payload()
    fake = FakeHistoricalAnalyticsClient(
        technical_result(),
        processed_result(payload["request_id"], replayed=True),
    )
    app.dependency_overrides[get_historical_analytics_client] = lambda: fake
    try:
        first = await post_submission(client, profile["id"], headers, payload)
        assert first.status_code == 503
        retry = await client.post(
            f"/api/v1/profiles/{profile['id']}/historical-submissions/"
            f"{first.json()['id']}/retry",
            headers=headers,
        )
    finally:
        app.dependency_overrides.pop(get_historical_analytics_client, None)

    assert retry.status_code == 200
    assert retry.json()["status"] == "PROCESSED"
    assert retry.json()["analytics_replayed"] is True
    assert fake.payloads[0] == fake.payloads[1]
    assert fake.payloads[0]["request_id"] == payload["request_id"]
    assert first.json()["id"] == retry.json()["id"]
    assert await db_session.scalar(
        select(func.count()).select_from(HistoricalSubmission)
    ) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "retryable"),
    [
        ("HISTORICAL_TIMEOUT", True),
        ("HISTORICAL_HTTP_503", True),
        ("HISTORICAL_INVALID_RESPONSE", True),
    ],
)
async def test_technical_failures_remain_persisted_for_retry(
    client,
    db_session,
    code,
    retryable,
):
    profile, _, headers = await create_profile_and_device(client, f"Failure {code}")
    fake = FakeHistoricalAnalyticsClient(technical_result(code, retryable=retryable))
    app.dependency_overrides[get_historical_analytics_client] = lambda: fake
    try:
        response = await post_submission(
            client,
            profile["id"],
            headers,
            historical_payload(),
        )
    finally:
        app.dependency_overrides.pop(get_historical_analytics_client, None)

    assert response.status_code == 503
    assert response.json()["status"] == "TECHNICAL_ERROR"
    assert response.json()["retryable"] is True
    stored = await db_session.scalar(select(HistoricalSubmission))
    assert stored.payload == fake.payloads[0]
    assert stored.last_error_code == code


@pytest.mark.asyncio
async def test_http_409_is_persisted_as_conflict_and_cannot_be_retried(
    client,
    db_session,
):
    profile, _, headers = await create_profile_and_device(client, "Conflict")
    conflict = HistoricalAnalyticsResult(
        kind="conflict",
        response={"error": "representation_conflict"},
        error_code="representation_conflict",
        error_message="Analytics conflict",
    )
    fake = FakeHistoricalAnalyticsClient(conflict)
    app.dependency_overrides[get_historical_analytics_client] = lambda: fake
    try:
        first = await post_submission(
            client,
            profile["id"],
            headers,
            historical_payload(),
        )
        retry = await client.post(
            f"/api/v1/profiles/{profile['id']}/historical-submissions/"
            f"{first.json()['id']}/retry",
            headers=headers,
        )
    finally:
        app.dependency_overrides.pop(get_historical_analytics_client, None)

    assert first.status_code == 409
    assert first.json()["status"] == "CONFLICT"
    assert retry.status_code == 409
    assert retry.json()["detail"]["code"] == "HISTORICAL_SUBMISSION_NOT_RETRYABLE"
    assert len(fake.payloads) == 1
    stored = await db_session.scalar(select(HistoricalSubmission))
    assert stored.analytics_response == {"error": "representation_conflict"}


@pytest.mark.asyncio
async def test_same_request_id_with_different_payload_is_conflict_without_overwrite(
    client,
    db_session,
):
    profile, _, headers = await create_profile_and_device(client, "Payload conflict")
    request_id = f"stable-{uuid.uuid4().hex}"
    first_payload = historical_payload(request_id=request_id, value=60.0)
    fake = FakeHistoricalAnalyticsClient(technical_result())
    app.dependency_overrides[get_historical_analytics_client] = lambda: fake
    try:
        first = await post_submission(client, profile["id"], headers, first_payload)
        changed = historical_payload(request_id=request_id, value=61.0)
        second = await post_submission(client, profile["id"], headers, changed)
    finally:
        app.dependency_overrides.pop(get_historical_analytics_client, None)

    assert first.status_code == 503
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "HISTORICAL_SUBMISSION_CONTENT_CONFLICT"
    stored = await db_session.scalar(select(HistoricalSubmission))
    assert stored.payload["representation"]["value"] == 60.0
    assert stored.attempts == 1
    assert len(fake.payloads) == 1


@pytest.mark.asyncio
async def test_invalid_iana_time_zone_is_422(client, historical_client):
    profile, _, headers = await create_profile_and_device(client, "Bad zone")
    payload = historical_payload()
    payload["time_zone"] = "Mars/Olympus_Mons"

    response = await post_submission(client, profile["id"], headers, payload)

    assert response.status_code == 422
    assert historical_client.payloads == []


@pytest.mark.asyncio
async def test_unresolved_previous_day_blocks_later_series_day(
    client,
    db_session,
):
    profile, _, headers = await create_profile_and_device(client, "Ordered")
    fake = FakeHistoricalAnalyticsClient(technical_result())
    app.dependency_overrides[get_historical_analytics_client] = lambda: fake
    try:
        first = await post_submission(
            client,
            profile["id"],
            headers,
            historical_payload(date(2026, 9, 1)),
        )
        second = await post_submission(
            client,
            profile["id"],
            headers,
            historical_payload(date(2026, 9, 2)),
        )
    finally:
        app.dependency_overrides.pop(get_historical_analytics_client, None)

    assert first.status_code == 503
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "HISTORICAL_SUBMISSION_ORDER_BLOCKED"
    assert len(fake.payloads) == 1
    assert await db_session.scalar(
        select(func.count()).select_from(HistoricalSubmission)
    ) == 1


@pytest.mark.asyncio
async def test_same_series_http_calls_are_serialized(client):
    profile, _, headers = await create_profile_and_device(client, "Serialized")

    class ControlledClient:
        def __init__(self):
            self.active = 0
            self.max_active = 0
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def evaluate(self, payload):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.started.set()
            await self.release.wait()
            self.active -= 1
            return processed_result(payload["request_id"])

    fake = ControlledClient()
    app.dependency_overrides[get_historical_analytics_client] = lambda: fake
    try:
        first_task = asyncio.create_task(
            post_submission(
                client,
                profile["id"],
                headers,
                historical_payload(date(2026, 9, 1)),
            )
        )
        await fake.started.wait()
        second_task = asyncio.create_task(
            post_submission(
                client,
                profile["id"],
                headers,
                historical_payload(date(2026, 9, 2)),
            )
        )
        await asyncio.sleep(0.05)
        assert fake.max_active == 1
        fake.release.set()
        first, second = await asyncio.gather(first_task, second_task)
    finally:
        app.dependency_overrides.pop(get_historical_analytics_client, None)

    assert first.status_code == second.status_code == 200
    assert fake.max_active == 1


@pytest.mark.asyncio
async def test_historical_client_sends_bearer_and_accepts_abstain():
    request_id = "client-bearer-test"

    async def handler(request):
        assert request.url.path == "/v1/historical/evaluate"
        assert request.headers["authorization"] == "Bearer service-secret"
        assert request.headers["content-type"].startswith("application/json")
        assert json.loads(request.content)["request_id"] == request_id
        return httpx.Response(200, json=processed_response(request_id))

    client = HistoricalAnalyticsClient(
        "http://analytics:8765",
        "service-secret",
        5,
        transport=httpx.MockTransport(handler),
    )

    result = await client.evaluate({"request_id": request_id, "representation": {}})

    assert result.kind == "processed"
    assert result.replayed is False
    assert result.evaluation_id == f"eval-{request_id}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected_code", "retryable"),
    [
        (httpx.Response(503, json={"error": "historical_unavailable"}), "HISTORICAL_HTTP_503", True),
        (httpx.Response(200, content=b"not-json"), "HISTORICAL_INVALID_RESPONSE", True),
        (httpx.Response(409, json={"error": "historical_order_conflict"}), "historical_order_conflict", False),
    ],
)
async def test_historical_client_classifies_http_responses(
    response,
    expected_code,
    retryable,
):
    async def handler(_request):
        return response

    client = HistoricalAnalyticsClient(
        "http://analytics:8765",
        "service-secret",
        5,
        transport=httpx.MockTransport(handler),
    )

    result = await client.evaluate({"request_id": "request-1", "representation": {}})

    assert result.error_code == expected_code
    assert result.retryable is retryable


@pytest.mark.asyncio
async def test_historical_client_classifies_timeout_as_retryable():
    async def handler(request):
        raise httpx.ReadTimeout("timeout", request=request)

    client = HistoricalAnalyticsClient(
        "http://analytics:8765",
        "service-secret",
        5,
        transport=httpx.MockTransport(handler),
    )

    result = await client.evaluate({"request_id": "request-1", "representation": {}})

    assert result.kind == "technical_error"
    assert result.error_code == "HISTORICAL_TIMEOUT"
    assert result.retryable is True


def test_historical_openapi_contract_separates_adult_read_from_device_writes():
    paths = app.openapi()["paths"]
    base = "/api/v1/profiles/{profile_id}/historical-submissions"

    assert paths[base]["get"]["security"] == [{"AdultBearer": []}]
    assert paths[base]["post"]["security"] == [{"DeviceBearer": []}]
    assert paths[f"{base}/{{submission_id}}/retry"]["post"]["security"] == [
        {"DeviceBearer": []}
    ]
