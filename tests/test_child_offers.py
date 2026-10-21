"""Fictitious profiles and simulated transport; no real offers are generated here."""

import copy
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select

from app.api.routes.profile_child_offers import get_child_offers_client
from app.db.models import ActivityOffer, ChildOfferOperation
from app.main import app
from app.services.child_offers_client import ChildOffersClient, ChildOffersError
from app.services.representative_verification_service import RepresentativeVerificationService


class FakeCoordinator:
    def __init__(self):
        self.calls = []
        self.offers = {}
        self.receipts = {}
        self.lose_once = False

    async def evaluate(self, payload):
        self.calls.append(("evaluate", copy.deepcopy(payload)))
        request_id = payload["request_id"]
        if request_id not in self.receipts:
            profile = payload["evidence"]["profile_id"]
            if not payload["evidence"]["capture_verified"]:
                receipt = {"status": "suppressed", "reason": "capture_unverified",
                           "policy_version": "automatic-child-offers-v1-engineering", "replayed": False}
            else:
                offer = {"offer_id": f"test-offer-{request_id}", "profile_id": profile,
                         "status": "pending", "expires_at": payload["evidence"]["now"]}
                self.offers.setdefault(profile, []).append(offer)
                receipt = {"status": "offer", "offer": offer, "channel": "overlay",
                           "policy_version": "automatic-child-offers-v1-engineering", "replayed": False}
            self.receipts[request_id] = receipt
        if self.lose_once:
            self.lose_once = False
            raise ChildOffersError("CHILD_OFFERS_TIMEOUT", 504)
        return {**self.receipts[request_id], "replayed": len([x for x in self.calls if x[1]["request_id"] == request_id]) > 1}

    async def event(self, payload):
        self.calls.append(("event", copy.deepcopy(payload)))
        if payload["request_id"] not in self.receipts:
            offer = next(x for x in self.offers[payload["profile_id"]] if x["offer_id"] == payload["offer_id"])
            offer["status"] = "delivered" if payload["event"] == "delivered" else "responded"
            self.receipts[payload["request_id"]] = {"status": "recorded", "offer": copy.deepcopy(offer),
                "policy_version": "automatic-child-offers-v1-engineering", "replayed": False}
        return {**self.receipts[payload["request_id"]], "replayed": len([x for x in self.calls if x[1]["request_id"] == payload["request_id"]]) > 1}

    async def history(self, profile_id):
        return {"offers": self.offers.get(profile_id, []), "events": []}


async def _test_profile(client, monkeypatch, adult_session, db_session):
    created = await client.post("/api/v1/profiles", json={
        "alias": "TEST_CHILD_OFFERS_FICTITIOUS", "age": 9, "avatar": "fox",
    })
    assert created.status_code == 201
    profile = created.json()
    registered = await client.post(f"/api/v1/profiles/{profile['id']}/devices",
        headers={"Authorization": f"Bearer {profile['bootstrap_token']}"},
        json={"installation_id": f"child-test-{uuid4()}", "platform": "ANDROID"})
    assert registered.status_code == 201
    device_headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    adult_headers, adult = await adult_session(profile["id"])
    await RepresentativeVerificationService(db_session).approve(adult.id, UUID(profile["id"]), verified_by="pytest-fictitious")
    monkeypatch.setattr("app.api.routes.profile_child_offers.get_settings", lambda: SimpleNamespace(
        environment="development", child_offers_test_enabled=True,
        child_offers_test_profile_ids=profile["id"],
    ))
    fake = FakeCoordinator()
    app.dependency_overrides[get_child_offers_client] = lambda: fake
    return profile["id"], device_headers, adult_headers, fake


def evaluation(profile_id, *, request_id=None, capture_verified=True):
    return {"request_id": request_id or f"test-eval-{uuid4()}", "evidence": {
        "profile_id": profile_id, "series_id": f"{profile_id}.series-1",
        "interval_id": f"{profile_id}.interval-1", "now": "2026-10-16T10:00:00-03:00",
        "interval_started_at": "2026-10-16T09:00:00-03:00",
        "bedtime_at": "2026-10-16T21:00:00-03:00", "automatic_enabled": True,
        "assignment_verified": True, "shared_phone": False, "capture_verified": capture_verified,
        "screen_interactive": True, "game_active": False, "in_school_period": False,
        "eligibility_state": "known", "eligibility": {}, "interests": [],
    }}


def path(profile_id, suffix):
    return f"/api/v1/profiles/{profile_id}/child-offers/{suffix}"


@pytest.mark.asyncio
async def test_test_gate_fails_closed(client, monkeypatch):
    created = await client.post("/api/v1/profiles", json={"alias": "TEST_CHILD_OFFERS_FICTITIOUS", "age": 9, "avatar": "fox"})
    profile = created.json()
    registered = await client.post(f"/api/v1/profiles/{profile['id']}/devices",
        headers={"Authorization": f"Bearer {profile['bootstrap_token']}"},
        json={"installation_id": f"test-{uuid4()}", "platform": "ANDROID"})
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    response = await client.post(path(profile["id"], "evaluate"), headers=headers, json=evaluation(profile["id"]))
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_offer_event_history_and_no_voluntary_write(client, monkeypatch, adult_session, db_session):
    profile_id, device, adult, fake = await _test_profile(client, monkeypatch, adult_session, db_session)
    try:
        payload = evaluation(profile_id)
        opened = await client.post(path(profile_id, "evaluate"), headers=device, json=payload)
        assert opened.status_code == 200
        offer_id = opened.json()["offer"]["offer_id"]
        delivered = {"request_id": f"test-event-{uuid4()}", "profile_id": profile_id,
            "offer_id": offer_id, "event": "delivered", "occurred_at": payload["evidence"]["now"],
            "now": payload["evidence"]["now"], "channel": "overlay"}
        response = await client.post(path(profile_id, "events"), headers=device, json=delivered)
        assert response.status_code == 200
        history = await client.get(path(profile_id, "history"), headers=adult)
        assert history.status_code == 200
        assert history.json()["offers"][0]["offer_id"] == offer_id
        assert await db_session.scalar(select(ActivityOffer).where(ActivityOffer.profile_id == UUID(profile_id))) is None
        assert (await client.post(path(profile_id, "events"), headers=device, json=delivered)).json()["replayed"]
        assert len([call for call in fake.calls if call[0] == "event"]) == 1
    finally:
        app.dependency_overrides.pop(get_child_offers_client, None)


@pytest.mark.asyncio
async def test_lost_response_retries_exact_payload_after_new_session(client, monkeypatch, adult_session, db_session_factory, db_session):
    profile_id, device, _adult, fake = await _test_profile(client, monkeypatch, adult_session, db_session)
    try:
        payload = evaluation(profile_id)
        fake.lose_once = True
        first = await client.post(path(profile_id, "evaluate"), headers=device, json=payload)
        assert first.status_code == 504
        async with db_session_factory() as new_session:
            operation = await new_session.get(ChildOfferOperation, payload["request_id"])
            assert operation.status == "PENDING" and operation.payload == payload
        other = evaluation(profile_id)
        assert (await client.post(path(profile_id, "evaluate"), headers=device, json=other)).status_code == 409
        retried = await client.post(path(profile_id, "evaluate"), headers=device, json=payload)
        assert retried.status_code == 200 and retried.json()["replayed"]
        assert fake.calls[0][1] == fake.calls[1][1]
        assert len(fake.offers[profile_id]) == 1
        altered = copy.deepcopy(payload)
        altered["evidence"]["capture_verified"] = False
        assert (await client.post(path(profile_id, "evaluate"), headers=device, json=altered)).status_code == 409
    finally:
        app.dependency_overrides.pop(get_child_offers_client, None)


@pytest.mark.asyncio
async def test_suppression_and_other_profile_denied(client, monkeypatch, adult_session, db_session):
    profile_id, device, adult, fake = await _test_profile(client, monkeypatch, adult_session, db_session)
    try:
        suppressed = await client.post(path(profile_id, "evaluate"), headers=device,
            json=evaluation(profile_id, capture_verified=False))
        assert suppressed.status_code == 200 and suppressed.json()["status"] == "suppressed"
        other = await client.post("/api/v1/profiles", json={"alias": "Other", "age": 9, "avatar": "fox"})
        other_id = other.json()["id"]
        assert (await client.post(path(other_id, "evaluate"), headers=device, json=evaluation(other_id))).status_code == 403
        assert (await client.get(path(other_id, "history"), headers=adult)).status_code == 403
        foreign_event = {"request_id": f"test-event-{uuid4()}", "profile_id": profile_id,
            "offer_id": "foreign-offer", "event": "delivered", "occurred_at": "2026-10-16T10:00:00-03:00",
            "now": "2026-10-16T10:00:00-03:00", "channel": "overlay"}
        assert (await client.post(path(profile_id, "events"), headers=device, json=foreign_event)).status_code == 409
        assert not any(call[0] == "event" for call in fake.calls)
        monkeypatch.setattr("app.api.routes.profile_child_offers.get_settings", lambda: SimpleNamespace(
            environment="production", child_offers_test_enabled=True,
            child_offers_test_profile_ids=profile_id,
        ))
        assert (await client.get(path(profile_id, "history"), headers=adult)).status_code == 404
    finally:
        app.dependency_overrides.pop(get_child_offers_client, None)


@pytest.mark.asyncio
async def test_http_client_preserves_contract_and_auth():
    calls = []

    def respond(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer fictitious-private-token"
        if request.url.path.endswith("/history"):
            return httpx.Response(200, json={"offers": [], "events": []})
        return httpx.Response(200, json={"status": "suppressed", "reason": "capture_unverified",
            "policy_version": "automatic-child-offers-v1-engineering", "replayed": False})

    service = ChildOffersClient("http://analytics.test", "fictitious-private-token", 1.5,
        transport=httpx.MockTransport(respond))
    profile = str(uuid4())
    assert (await service.evaluate(evaluation(profile)))["status"] == "suppressed"
    assert (await service.history(profile))["offers"] == []
    assert [call.method for call in calls] == ["POST", "GET"]


@pytest.mark.asyncio
@pytest.mark.parametrize("upstream,expected", [(409, 409), (503, 503), (400, 400)])
async def test_http_client_preserves_coordinator_errors(upstream, expected):
    def respond(_request):
        return httpx.Response(upstream, json={"error": "private-upstream-detail"})

    service = ChildOffersClient("http://analytics.test", "fictitious-private-token", 1,
        transport=httpx.MockTransport(respond))
    with pytest.raises(ChildOffersError) as failure:
        await service.evaluate(evaluation(str(uuid4())))
    assert failure.value.status_code == expected
    assert "private-upstream-detail" not in str(failure.value)


@pytest.mark.asyncio
async def test_http_client_rejects_foreign_profile_and_timeout():
    profile = str(uuid4())

    def foreign(_request):
        return httpx.Response(200, json={"offers": [{"offer_id": "foreign", "profile_id": str(uuid4()),
            "status": "pending", "expires_at": "2026-10-16T10:10:00-03:00"}], "events": []})

    service = ChildOffersClient("http://analytics.test", "fictitious-private-token", 1,
        transport=httpx.MockTransport(foreign))
    with pytest.raises(ChildOffersError) as failure:
        await service.history(profile)
    assert failure.value.status_code == 502

    def timeout(request):
        raise httpx.ReadTimeout("simulated timeout", request=request)

    service = ChildOffersClient("http://analytics.test", "fictitious-private-token", 1,
        transport=httpx.MockTransport(timeout))
    with pytest.raises(ChildOffersError) as failure:
        await service.history(profile)
    assert failure.value.status_code == 504
