"""Integracion con JWT/SQLite temporal y transporte Analytics simulado.

La fixture de tarjetas es el ejemplo ficticio de Analytics 04f1e3a, tomado de
docs/integration/examples/adult_messages_2026-10-13/history.fictitious.json.
No se evalua ni se siembra informacion en servicios o perfiles reales.
"""

import copy
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import httpx
import pytest
from pydantic import SecretStr

from app.api.routes.profile_adult_messages import get_adult_messages_client
from app.core.config import get_settings
from app.db.models import Profile
from app.main import app
from app.services.adult_messages_client import AdultMessagesClient, AdultMessagesError
from app.services.adult_profile_service import AdultProfileService
from app.services.representative_verification_service import RepresentativeVerificationService


TOKEN = "fictitious-analytics-service-token"
FIXTURE = Path(__file__).parent / "fixtures" / "adult_messages_history.fictitious.json"


@pytest.fixture
def analytics(client):
    state = SimpleNamespace(payload={"versions": []}, status=200, error=None, calls=[])

    def respond(request):
        state.calls.append(request)
        assert request.method == "GET"
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        if state.error == "timeout":
            raise httpx.ReadTimeout(TOKEN, request=request)
        if state.error == "connection":
            raise httpx.ConnectError(TOKEN, request=request)
        return httpx.Response(state.status, json=state.payload)

    service = AdultMessagesClient("http://analytics.test/", TOKEN, 2.5,
                                  transport=httpx.MockTransport(respond))
    app.dependency_overrides[get_adult_messages_client] = lambda: service
    yield state
    app.dependency_overrides.pop(get_adult_messages_client, None)


@pytest.fixture
async def adult_profile(client, adult_session, db_session):
    created = await client.post("/api/v1/profiles", json={"alias": "Fictitious adult messages", "age": 9, "avatar": "fox"})
    assert created.status_code == 201
    profile_id = uuid.UUID(created.json()["id"])
    headers, adult = await adult_session(profile_id)
    await RepresentativeVerificationService(db_session).approve(adult.id, profile_id, verified_by="pytest-fictitious")
    return profile_id, headers, adult


def history_for(profile_id):
    history = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for item in history["versions"]:
        item["card"]["profile_id"] = str(profile_id)
    return history


def path(profile_id):
    return f"/api/v1/profiles/{profile_id}/adult-messages/history"


async def test_history_preserves_text_versions_and_get_is_repeatable(client, adult_profile, analytics):
    profile_id, headers, _ = adult_profile
    analytics.payload = history_for(profile_id)
    original = copy.deepcopy(analytics.payload)
    for _ in range(2):
        response = await client.get(path(profile_id), headers=headers)
        assert response.status_code == 200
        assert response.json() == original
        assert response.headers["cache-control"] == "no-store"
        assert TOKEN not in response.text
    assert len(analytics.calls) == 2
    assert all(str(call.url) == f"http://analytics.test/v1/adult-messages/profiles/{profile_id}/history" for call in analytics.calls)
    assert all(call.extensions["timeout"]["read"] == 2.5 for call in analytics.calls)
    assert analytics.payload == original


async def test_empty_history_is_not_an_unavailability_error(client, adult_profile, analytics):
    profile_id, headers, _ = adult_profile
    response = await client.get(path(profile_id), headers=headers)
    assert response.status_code == 200
    assert response.json() == {"versions": []}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer invalid-token"}])
async def test_authentication_required_before_analytics(client, adult_profile, analytics, headers):
    response = await client.get(path(adult_profile[0]), headers=headers)
    assert response.status_code == 401
    assert analytics.calls == []


async def test_device_token_is_not_an_adult(client, adult_profile, analytics):
    created = await client.post("/api/v1/profiles", json={"alias": "Fictitious device", "age": 9, "avatar": "fox"})
    own = created.json()
    registered = await client.post(f"/api/v1/profiles/{own['id']}/devices",
                                   headers={"Authorization": f"Bearer {own['bootstrap_token']}"},
                                   json={"installation_id": "fictitious-adult-message-device", "platform": "ANDROID"})
    assert registered.status_code == 201
    response = await client.get(path(own["id"]), headers={"Authorization": f"Bearer {registered.json()['access_token']}"})
    assert response.status_code == 401
    assert analytics.calls == []


async def test_other_profile_and_nonexistent_profile_are_denied(client, adult_profile, analytics):
    profile_id, headers, _ = adult_profile
    other = await client.post("/api/v1/profiles", json={"alias": "Other fictitious profile", "age": 8, "avatar": "fox"})
    for denied_id in (other.json()["id"], uuid.uuid4()):
        response = await client.get(path(denied_id), headers=headers)
        assert response.status_code == 403
    assert analytics.calls == []


@pytest.mark.parametrize("access", ["pending", "rejected", "revoked", "inactive"])
async def test_verification_link_and_active_profile_checked_each_time(client, db_session, adult_profile, analytics, access):
    profile_id, headers, adult = adult_profile
    if access == "pending":
        await RepresentativeVerificationService(db_session).set_pending(adult.id, profile_id)
    elif access == "rejected":
        await RepresentativeVerificationService(db_session).reject(adult.id, profile_id, verified_by="pytest")
    elif access == "revoked":
        await AdultProfileService(db_session).revoke_link(adult, profile_id)
    else:
        profile = await db_session.get(Profile, profile_id)
        profile.active = False
        await db_session.commit()
    response = await client.get(path(profile_id), headers=headers)
    assert response.status_code == 403
    assert analytics.calls == []


@pytest.mark.parametrize(("error", "upstream", "expected", "retryable"), [
    ("timeout", 200, 504, True), ("connection", 200, 503, True),
    (None, 503, 503, True), (None, 500, 503, True), (None, 429, 503, True),
    (None, 401, 502, False), (None, 400, 502, False), (None, 302, 502, False),
])
async def test_transport_errors_do_not_become_empty_history_or_leak_details(client, adult_profile, analytics, error, upstream, expected, retryable):
    profile_id, headers, _ = adult_profile
    analytics.error, analytics.status = error, upstream
    analytics.payload = {"error": TOKEN}
    response = await client.get(path(profile_id), headers=headers)
    assert response.status_code == expected
    assert response.json()["detail"]["retryable"] is retryable
    assert TOKEN not in response.text
    assert "versions" not in response.json()
    assert response.headers["cache-control"] == "no-store"
    assert len(analytics.calls) == 1  # Sin bucles automaticos.


async def test_safe_get_retry_after_unavailability(client, adult_profile, analytics):
    profile_id, headers, _ = adult_profile
    analytics.status = 503
    failed = await client.get(path(profile_id), headers=headers)
    assert failed.status_code == 503
    analytics.status, analytics.payload = 200, history_for(profile_id)
    recovered = await client.get(path(profile_id), headers=headers)
    repeated = await client.get(path(profile_id), headers=headers)
    assert recovered.status_code == repeated.status_code == 200
    assert recovered.json() == repeated.json() == analytics.payload
    assert all(call.method == "GET" for call in analytics.calls)


@pytest.mark.parametrize("mutation", ["profile", "missing", "extra", "version", "duplicate", "statistics", "date"])
async def test_contract_errors_reject_the_entire_history(client, adult_profile, analytics, mutation):
    profile_id, headers, _ = adult_profile
    analytics.payload = history_for(profile_id)
    item = analytics.payload["versions"][1]
    if mutation == "profile":
        item["card"]["profile_id"] = str(uuid.uuid4())
    elif mutation == "missing":
        del item["card"]["recommendation"]
    elif mutation == "extra":
        item["card"]["private_token"] = TOKEN
    elif mutation == "version":
        item["version"] = True
    elif mutation == "duplicate":
        analytics.payload["versions"].append(copy.deepcopy(item))
    elif mutation == "statistics":
        item["card"]["statistics"]["delta"] = "40"
    else:
        item["card"]["evaluation_date"] = "invalid"
    response = await client.get(path(profile_id), headers=headers)
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "ADULT_MESSAGES_INVALID_RESPONSE"
    assert TOKEN not in response.text
    assert "card" not in response.json()


async def test_corrections_and_null_statistics_are_preserved(client, adult_profile, analytics):
    profile_id, headers, _ = adult_profile
    analytics.payload = history_for(profile_id)
    card = analytics.payload["versions"][-1]["card"]
    card.update(status="CORRECTED_HISTORY", statistics=None,
                updated_date="2026-02-23", corrected_dates=["2026-01-06"])
    response = await client.get(path(profile_id), headers=headers)
    assert response.status_code == 200
    assert response.json() == analytics.payload


@pytest.mark.parametrize("content", [b'not json', b'[]', b'{"versions":null}', b'{"versions":[],"versions":[]}', b'{"versions":NaN}', b'\xff'])
async def test_invalid_json_is_a_contract_error(content):
    service = AdultMessagesClient("http://analytics.test", TOKEN, 1,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content)))
    with pytest.raises(AdultMessagesError) as error:
        await service.history(uuid.uuid4())
    assert error.value.code == "ADULT_MESSAGES_INVALID_RESPONSE"


async def test_missing_service_token_fails_closed(client, adult_profile, monkeypatch):
    monkeypatch.setattr(get_settings(), "analytics_service_token", None)
    response = await client.get(path(adult_profile[0]), headers=adult_profile[1])
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "ADULT_MESSAGES_NOT_CONFIGURED"


def test_client_uses_existing_url_token_and_timeout(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "analytics_service_url", "http://analytics.test/")
    monkeypatch.setattr(settings, "analytics_service_token", SecretStr(TOKEN))
    monkeypatch.setattr(settings, "analytics_timeout_seconds", 3.5)
    service = get_adult_messages_client()
    assert service.service_url == "http://analytics.test"
    assert service.service_token == TOKEN
    assert service.timeout_seconds == 3.5


def test_openapi_exposes_only_read_with_adult_bearer():
    paths = app.openapi()["paths"]
    entry = paths[path("{profile_id}")]
    assert set(entry) == {"get"}
    assert entry["get"]["security"] == [{"AdultBearer": []}]
    assert not any("adult-messages" in key and "evaluate" in key for key in paths)


async def test_no_generation_route_or_analytics_post(client, adult_profile, analytics):
    profile_id, headers, _ = adult_profile
    response = await client.post(f"/api/v1/profiles/{profile_id}/adult-messages/evaluate", headers=headers, json={})
    assert response.status_code == 404
    assert analytics.calls == []
