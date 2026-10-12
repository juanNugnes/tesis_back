import json
import uuid

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.routes.experimental_pmum import get_experimental_pmum_client
from app.db.models import (
    ExperimentalPmumEstimate,
    ExperimentalPmumEstimateStatus,
    ParentalRespondent,
    ParentalScreenTimeResponse,
)
from app.main import app
from app.services.experimental_pmum_client import (
    ExperimentalPmumClient,
    ExperimentalPmumRequest,
    ExperimentalPmumResult,
)


class FakeExperimentalPmumClient:
    def __init__(self):
        self.result = ExperimentalPmumResult(
            status=ExperimentalPmumEstimateStatus.ESTIMATED,
            estimated_pmum_sf_mean=2.6959,
            model_version="hu_pmum_sf_experimental_v1",
        )
        self.calls = []

    async def estimate(self, data):
        self.calls.append(data.model_dump(mode="json"))
        return self.result


@pytest.fixture
def fake_pmum_client(client):
    fake = FakeExperimentalPmumClient()
    app.dependency_overrides[get_experimental_pmum_client] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_experimental_pmum_client, None)


async def create_profile_and_device(client, *, age=9):
    created = await client.post(
        "/api/v1/profiles",
        json={"alias": "PMUM experimental", "age": age, "avatar": "fox"},
    )
    assert created.status_code == 201
    profile = created.json()
    registered = await client.post(
        f"/api/v1/profiles/{profile['id']}/devices",
        headers={"Authorization": f"Bearer {profile['bootstrap_token']}"},
        json={"installation_id": f"pmum-{uuid.uuid4()}", "platform": "ANDROID"},
    )
    assert registered.status_code == 201
    return profile, {"Authorization": f"Bearer {registered.json()['access_token']}"}


async def authenticate_adult(client, email):
    requested = await client.post("/api/v1/auth/adult/otp/request", json={"email": email})
    assert requested.status_code == 202
    verified = await client.post(
        "/api/v1/auth/adult/otp/verify",
        json={"email": email, "code": requested.json()["dev_code"]},
    )
    assert verified.status_code == 200
    return {"Authorization": f"Bearer {verified.json()['access_token']}"}


def response_payload(**overrides):
    payload = {
        "sex_code": 1,
        "weekday_category": 2,
        "weekend_category": 3,
        "respondent": "parent_or_caregiver",
        "reference_days": 30,
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_linked_adult_creates_estimated_revision_with_minimal_python_payload(
    client,
    db_session,
    adult_session,
    fake_pmum_client,
):
    profile, _ = await create_profile_and_device(client, age=9)
    headers, adult = await adult_session(profile["id"], email="pmum-owner@example.com")

    response = await client.post(
        f"/api/v1/profiles/{profile['id']}/experimental-pmum/responses",
        headers=headers,
        json=response_payload(),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["response"]["adult_id"] == str(adult.id)
    assert body["response"]["age"] == 9
    assert body["response"]["revision"] == 1
    assert body["estimate"]["status"] == "estimated"
    assert body["estimate"]["estimated_pmum_sf_mean"] == pytest.approx(2.6959)
    assert body["estimate"]["model_version"] == "hu_pmum_sf_experimental_v1"
    assert fake_pmum_client.calls == [
        {
            "age": 9,
            "sex_code": 1,
            "weekday_category": 2,
            "weekend_category": 3,
            "respondent": "parent_or_caregiver",
            "reference_days": 30,
        }
    ]
    assert await db_session.scalar(
        select(func.count()).select_from(ExperimentalPmumEstimate)
    ) == 1


@pytest.mark.asyncio
async def test_experimental_pmum_is_adult_only_link_scoped_and_rejects_adult_id(
    client,
    db_session,
    adult_session,
    fake_pmum_client,
):
    profile, device_headers = await create_profile_and_device(client)
    unlinked_headers = await authenticate_adult(client, "pmum-unlinked@example.com")
    linked_headers, _ = await adult_session(profile["id"], email="pmum-linked@example.com")
    url = f"/api/v1/profiles/{profile['id']}/experimental-pmum/responses"

    unlinked = await client.post(url, headers=unlinked_headers, json=response_payload())
    device = await client.post(url, headers=device_headers, json=response_payload())
    injected = await client.post(
        url,
        headers=linked_headers,
        json=response_payload(adult_id=str(uuid.uuid4())),
    )

    assert unlinked.status_code == 403
    assert device.status_code == 401
    assert injected.status_code == 422
    assert fake_pmum_client.calls == []
    assert await db_session.scalar(
        select(func.count()).select_from(ParentalScreenTimeResponse)
    ) == 0


@pytest.mark.asyncio
async def test_revisions_are_immutable_age_snapshots_and_latest_returns_revision_two(
    client,
    db_session,
    adult_session,
    fake_pmum_client,
):
    profile, _ = await create_profile_and_device(client, age=9)
    headers, adult = await adult_session(profile["id"], email="pmum-revisions@example.com")
    url = f"/api/v1/profiles/{profile['id']}/experimental-pmum"

    first = await client.post(
        f"{url}/responses",
        headers=headers,
        json=response_payload(weekday_category=1),
    )
    updated_profile = await client.put(
        f"/api/v1/profiles/{profile['id']}",
        headers=headers,
        json={"age": 10},
    )
    second = await client.post(
        f"{url}/responses",
        headers=headers,
        json=response_payload(weekday_category=4),
    )
    latest = await client.get(f"{url}/latest", headers=headers)

    assert first.status_code == second.status_code == 201
    assert updated_profile.status_code == 200
    assert first.json()["response"]["revision"] == 1
    assert first.json()["response"]["age"] == 9
    assert second.json()["response"]["revision"] == 2
    assert second.json()["response"]["age"] == 10
    assert latest.status_code == 200
    assert latest.json()["response"]["revision"] == 2
    assert latest.json()["response"]["weekday_category"] == 4

    stored = list(
        (
            await db_session.execute(
                select(ParentalScreenTimeResponse)
                .where(ParentalScreenTimeResponse.profile_id == uuid.UUID(profile["id"]))
                .order_by(ParentalScreenTimeResponse.revision)
            )
        ).scalars()
    )
    assert [(item.revision, item.age, item.weekday_category) for item in stored] == [
        (1, 9, 1),
        (2, 10, 4),
    ]

    duplicate = ParentalScreenTimeResponse(
        profile_id=uuid.UUID(profile["id"]),
        adult_id=adult.id,
        revision=2,
        age=10,
        sex_code=None,
        weekday_category=None,
        weekend_category=None,
        respondent=ParentalRespondent.PARENT_OR_CAREGIVER,
        reference_days=30,
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_null_sex_persists_unavailable_revision_without_calling_pmum(
    client,
    db_session,
    adult_session,
    fake_pmum_client,
):
    profile, _ = await create_profile_and_device(client)
    headers, _ = await adult_session(profile["id"], email="pmum-unavailable@example.com")

    response = await client.post(
        f"/api/v1/profiles/{profile['id']}/experimental-pmum/responses",
        headers=headers,
        json=response_payload(sex_code=None),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["estimate"]["status"] == "unavailable"
    assert body["estimate"]["estimated_pmum_sf_mean"] is None
    assert body["estimate"]["model_version"] is None
    assert body["estimate"]["technical_error_code"] is None
    assert body["estimate"]["technical_error_message"] is None
    assert body["response"]["sex_code"] is None
    assert body["response"]["weekday_category"] == 2
    assert body["response"]["weekend_category"] == 3
    assert fake_pmum_client.calls == []
    assert await db_session.scalar(
        select(func.count()).select_from(ParentalScreenTimeResponse)
    ) == 1
    assert await db_session.scalar(
        select(func.count()).select_from(ExperimentalPmumEstimate)
    ) == 1


@pytest.mark.asyncio
async def test_missing_parent_answer_unavailable_still_calls_pmum(
    client,
    adult_session,
    fake_pmum_client,
):
    profile, _ = await create_profile_and_device(client)
    headers, _ = await adult_session(
        profile["id"],
        email="pmum-missing-answer@example.com",
    )
    fake_pmum_client.result = ExperimentalPmumResult(
        status=ExperimentalPmumEstimateStatus.UNAVAILABLE,
        model_version="hu_pmum_sf_experimental_v1",
    )

    response = await client.post(
        f"/api/v1/profiles/{profile['id']}/experimental-pmum/responses",
        headers=headers,
        json=response_payload(weekday_category=None),
    )

    assert response.status_code == 201
    assert response.json()["estimate"]["status"] == "unavailable"
    assert fake_pmum_client.calls == [
        {
            "age": 9,
            "sex_code": 1,
            "weekday_category": None,
            "weekend_category": 3,
            "respondent": "parent_or_caregiver",
            "reference_days": 30,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_code",
    ["TIMEOUT", "CONNECTION_ERROR", "HTTP_ERROR", "INVALID_RESPONSE"],
)
async def test_technical_errors_are_persisted_without_losing_parental_revision(
    client,
    db_session,
    adult_session,
    fake_pmum_client,
    error_code,
):
    profile, _ = await create_profile_and_device(client)
    headers, _ = await adult_session(
        profile["id"],
        email=f"pmum-{error_code.lower()}@example.com",
    )
    fake_pmum_client.result = ExperimentalPmumResult.technical_error(
        error_code,
        "Bounded technical message",
    )

    response = await client.post(
        f"/api/v1/profiles/{profile['id']}/experimental-pmum/responses",
        headers=headers,
        json=response_payload(),
    )

    assert response.status_code == 201
    assert response.json()["estimate"]["status"] == "technical_error"
    assert response.json()["estimate"]["technical_error_code"] == error_code
    assert response.json()["estimate"]["estimated_pmum_sf_mean"] is None
    assert await db_session.scalar(
        select(func.count()).select_from(ParentalScreenTimeResponse)
    ) == 1
    assert await db_session.scalar(
        select(func.count()).select_from(ExperimentalPmumEstimate)
    ) == 1


@pytest.mark.asyncio
async def test_complete_data_connection_failure_persists_technical_error(
    client,
    db_session,
    adult_session,
):
    profile, _ = await create_profile_and_device(client)
    headers, _ = await adult_session(
        profile["id"],
        email="pmum-connection-failure@example.com",
    )

    async def connection_failure(request):
        raise httpx.ConnectError("connection refused", request=request)

    failing_client = ExperimentalPmumClient(
        "http://analytics.test",
        "analytics-service-token",
        1,
        transport=httpx.MockTransport(connection_failure),
    )
    app.dependency_overrides[get_experimental_pmum_client] = lambda: failing_client
    try:
        response = await client.post(
            f"/api/v1/profiles/{profile['id']}/experimental-pmum/responses",
            headers=headers,
            json=response_payload(),
        )
    finally:
        app.dependency_overrides.pop(get_experimental_pmum_client, None)

    assert response.status_code == 201
    assert response.json()["estimate"]["status"] == "technical_error"
    assert response.json()["estimate"]["technical_error_code"] == "CONNECTION_ERROR"
    assert await db_session.scalar(
        select(func.count()).select_from(ParentalScreenTimeResponse)
    ) == 1
    assert await db_session.scalar(
        select(func.count()).select_from(ExperimentalPmumEstimate)
    ) == 1


@pytest.mark.asyncio
async def test_latest_returns_404_when_profile_has_no_parental_response(
    client,
    adult_session,
    fake_pmum_client,
):
    profile, _ = await create_profile_and_device(client)
    headers, _ = await adult_session(profile["id"], email="pmum-empty@example.com")

    response = await client.get(
        f"/api/v1/profiles/{profile['id']}/experimental-pmum/latest",
        headers=headers,
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "No experimental PMUM response found"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "expected_code"),
    [
        ("timeout", "TIMEOUT"),
        ("connection", "CONNECTION_ERROR"),
        ("http", "HTTP_ERROR"),
        ("invalid", "INVALID_RESPONSE"),
    ],
)
async def test_http_client_classifies_technical_failures(scenario, expected_code):
    async def handler(request):
        if scenario == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        if scenario == "connection":
            raise httpx.ConnectError("connection refused", request=request)
        if scenario == "http":
            return httpx.Response(503, request=request)
        return httpx.Response(200, text="not-json", request=request)

    client = ExperimentalPmumClient(
        "http://analytics.test",
        "analytics-service-token",
        1,
        transport=httpx.MockTransport(handler),
    )
    result = await client.estimate(
        ExperimentalPmumRequest(
            age=9,
            sex_code=1,
            weekday_category=2,
            weekend_category=3,
            respondent=ParentalRespondent.PARENT_OR_CAREGIVER,
            reference_days=30,
        )
    )

    assert result.status == ExperimentalPmumEstimateStatus.TECHNICAL_ERROR
    assert result.technical_error_code == expected_code
    assert result.estimated_pmum_sf_mean is None


@pytest.mark.asyncio
async def test_http_client_preserves_functional_unavailable_response():
    async def handler(request):
        return httpx.Response(
            200,
            request=request,
            json={
                "status": "unavailable",
                "reason": "missing_parent_answer",
            },
        )

    client = ExperimentalPmumClient(
        "http://analytics.test",
        "analytics-service-token",
        1,
        transport=httpx.MockTransport(handler),
    )

    result = await client.estimate(
        ExperimentalPmumRequest(
            age=9,
            sex_code=1,
            weekday_category=None,
            weekend_category=3,
            respondent=ParentalRespondent.PARENT_OR_CAREGIVER,
            reference_days=30,
        )
    )

    assert result.status == ExperimentalPmumEstimateStatus.UNAVAILABLE
    assert result.technical_error_code is None
    assert result.estimated_pmum_sf_mean is None


@pytest.mark.asyncio
async def test_http_client_sends_only_six_non_identifying_inputs():
    captured = {"payload": None, "authorization": None, "content_type": None}

    async def handler(request):
        captured["payload"] = json.loads(request.content)
        captured["authorization"] = request.headers.get("Authorization")
        captured["content_type"] = request.headers.get("Content-Type")
        return httpx.Response(
            200,
            request=request,
            json={
                "status": "estimated",
                "estimated_pmum_sf_mean": 2.6959,
                "model_version": "hu_pmum_sf_experimental_v1",
            },
        )

    client = ExperimentalPmumClient(
        "http://analytics.test/",
        "analytics-service-token",
        1,
        transport=httpx.MockTransport(handler),
    )
    result = await client.estimate(
        ExperimentalPmumRequest(
            age=9,
            sex_code=1,
            weekday_category=2,
            weekend_category=3,
            respondent=ParentalRespondent.PARENT_OR_CAREGIVER,
            reference_days=30,
        )
    )

    assert result.status == ExperimentalPmumEstimateStatus.ESTIMATED
    assert captured["payload"] == {"age": 9, **response_payload()}
    assert captured["authorization"] == "Bearer analytics-service-token"
    assert captured["content_type"] == "application/json"


def test_experimental_pmum_openapi_contract_is_adult_only():
    schema = app.openapi()
    paths = schema["paths"]
    create = paths["/api/v1/profiles/{profile_id}/experimental-pmum/responses"]["post"]
    latest = paths["/api/v1/profiles/{profile_id}/experimental-pmum/latest"]["get"]
    request_schema = schema["components"]["schemas"]["ParentalScreenTimeResponseCreate"]

    assert create["security"] == latest["security"] == [{"AdultBearer": []}]
    assert set(request_schema["properties"]) == {
        "sex_code",
        "weekday_category",
        "weekend_category",
        "respondent",
        "reference_days",
    }
    assert request_schema["additionalProperties"] is False
