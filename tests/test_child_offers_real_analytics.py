"""Opt-in: real Analytics HTTP coordinator against a new, isolated test SQLite file."""

import asyncio
import importlib
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest

from app.services.child_offers_client import ChildOffersClient
from app.api.routes.profile_child_offers import get_child_offers_client
from app.main import app
from app.services.representative_verification_service import RepresentativeVerificationService


@pytest.mark.asyncio
async def test_real_coordinator_http_replay_history_and_events(
    tmp_path, monkeypatch, client, adult_session, db_session,
):
    source = os.environ.get("ANALYTICS_SOURCE_DIR")
    if not source or not (Path(source) / "analytics_service" / "child_offers.py").is_file():
        pytest.skip("Set ANALYTICS_SOURCE_DIR to the extracted Analytics source for isolated integration")
    monkeypatch.syspath_prepend(source)
    coordinator_module = importlib.import_module("analytics_service.child_offers")
    http_module = importlib.import_module("analytics_service.http")
    settings_module = importlib.import_module("analytics_service.settings")
    store = tmp_path / "isolated-fictitious-coordinator.sqlite"
    coordinator_module.ChildOfferCoordinator(store, create=True)
    token = "fictitious-private-token-0001"
    analytics_app = http_module.create_app(settings_module.Settings(
        token=token, child_offers_enabled=True, child_offers_repository_path=store,
    ))
    profile = str(uuid4())
    evidence = {
        "profile_id": profile, "series_id": f"{profile}.series-1",
        "interval_id": f"{profile}.interval-1", "now": "2026-10-16T10:00:00-03:00",
        "interval_started_at": "2026-10-16T09:00:00-03:00",
        "bedtime_at": "2026-10-16T21:00:00-03:00", "automatic_enabled": True,
        "assignment_verified": True, "shared_phone": False, "capture_verified": True,
        "screen_interactive": True, "game_active": False, "in_school_period": False,
        "eligibility_state": "known", "eligibility": {}, "interests": [],
    }
    transport = httpx.ASGITransport(app=analytics_app)
    analytics_client = ChildOffersClient("http://analytics.test", token, 5, transport=transport)
    async with analytics_app.router.lifespan_context(analytics_app):
        request = {"request_id": f"test-eval-{uuid4()}", "evidence": evidence}
        first = await analytics_client.evaluate(request)
        assert first["status"] == "offer" and first["replayed"] is False
        same = await analytics_client.evaluate(request)
        assert same["replayed"] is True and same["offer"]["offer_id"] == first["offer"]["offer_id"]
        offer_id = first["offer"]["offer_id"]
        delivery = {"request_id": f"test-delivery-{uuid4()}", "profile_id": profile,
            "offer_id": offer_id, "event": "delivered", "occurred_at": evidence["now"],
            "now": evidence["now"], "channel": first["channel"]}
        delivered = await analytics_client.event(delivery)
        assert delivered["status"] == "recorded"
        selected = {"request_id": f"test-selection-{uuid4()}", "profile_id": profile,
            "offer_id": offer_id, "event": "selected", "occurred_at": evidence["now"],
            "now": evidence["now"],
            "selected_activity_id": first["offer"]["activities_offered"][0]}
        selected_result = await analytics_client.event(selected)
        assert selected_result["offer"]["response"] == "selected"
        history = await analytics_client.history(profile)
        assert history["offers"][0]["offer_id"] == offer_id
        assert history["offers"][0]["status"] == "responded"
        assert len(history["events"]) == 3
        for response_name in ("rejected", "postponed", "dismissed"):
            another = str(uuid4())
            other_evidence = {**evidence, "profile_id": another,
                "series_id": f"{another}.series-1", "interval_id": f"{another}.interval-1"}
            other_offer = await analytics_client.evaluate({"request_id": f"test-eval-{uuid4()}", "evidence": other_evidence})
            assert other_offer["status"] == "offer"
            another_offer_id = other_offer["offer"]["offer_id"]
            await analytics_client.event({"request_id": f"test-delivery-{uuid4()}", "profile_id": another,
                "offer_id": another_offer_id, "event": "delivered", "occurred_at": evidence["now"],
                "now": evidence["now"], "channel": other_offer["channel"]})
            terminal = await analytics_client.event({"request_id": f"test-response-{uuid4()}",
                "profile_id": another, "offer_id": another_offer_id, "event": response_name,
                "occurred_at": evidence["now"], "now": evidence["now"]})
            assert terminal["offer"]["response"] == ("ignored" if response_name == "dismissed" else response_name)
        suppressed_profile = str(uuid4())
        suppressed = await analytics_client.evaluate({"request_id": f"test-eval-{uuid4()}", "evidence": {
            **evidence, "profile_id": suppressed_profile,
            "series_id": f"{suppressed_profile}.series-1",
            "interval_id": f"{suppressed_profile}.interval-1", "capture_verified": False,
        }})
        assert suppressed["status"] == "suppressed" and suppressed["reason"] == "capture_unverified"
        concurrent_profile = str(uuid4())
        concurrent_request = {"request_id": f"test-eval-{uuid4()}", "evidence": {
            **evidence, "profile_id": concurrent_profile,
            "series_id": f"{concurrent_profile}.series-1",
            "interval_id": f"{concurrent_profile}.interval-1",
        }}
        concurrent_results = await asyncio.gather(
            analytics_client.evaluate(concurrent_request),
            analytics_client.evaluate(concurrent_request),
        )
        assert concurrent_results[0]["offer"]["offer_id"] == concurrent_results[1]["offer"]["offer_id"]
        assert sorted(result["replayed"] for result in concurrent_results) == [False, True]
        assert len((await analytics_client.history(concurrent_profile))["offers"]) == 1
        # End-to-end backend HTTP -> private client -> genuine Analytics HTTP.
        created = await client.post("/api/v1/profiles", json={
            "alias": "TEST_CHILD_OFFERS_REAL_COORDINATOR", "age": 9, "avatar": "fox",
        })
        assert created.status_code == 201
        test_profile = created.json()
        test_id = test_profile["id"]
        registered = await client.post(f"/api/v1/profiles/{test_id}/devices",
            headers={"Authorization": f"Bearer {test_profile['bootstrap_token']}"},
            json={"installation_id": f"test-real-coordinator-{uuid4()}", "platform": "ANDROID"})
        assert registered.status_code == 201
        device_headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
        adult_headers, adult = await adult_session(test_id)
        await RepresentativeVerificationService(db_session).approve(
            adult.id, UUID(test_id), verified_by="pytest-fictitious",
        )
        monkeypatch.setattr("app.api.routes.profile_child_offers.get_settings", lambda: SimpleNamespace(
            environment="development", child_offers_test_enabled=True,
            child_offers_test_profile_ids=test_id,
        ))
        app.dependency_overrides[get_child_offers_client] = lambda: analytics_client
        try:
            backend_evidence = {**evidence, "profile_id": test_id,
                "series_id": f"{test_id}.series-1", "interval_id": f"{test_id}.interval-1"}
            path = f"/api/v1/profiles/{test_id}/child-offers"
            backend_offer = await client.post(path + "/evaluate", headers=device_headers,
                json={"request_id": f"test-eval-{uuid4()}", "evidence": backend_evidence})
            assert backend_offer.status_code == 200
            backend_offer_id = backend_offer.json()["offer"]["offer_id"]
            backend_delivery = await client.post(path + "/events", headers=device_headers, json={
                "request_id": f"test-event-{uuid4()}", "profile_id": test_id,
                "offer_id": backend_offer_id, "event": "delivered",
                "occurred_at": evidence["now"], "now": evidence["now"], "channel": "overlay",
            })
            assert backend_delivery.status_code == 200
            backend_history = await client.get(path + "/history", headers=adult_headers)
            assert backend_history.status_code == 200
            assert backend_history.json()["offers"][0]["offer_id"] == backend_offer_id
        finally:
            app.dependency_overrides.pop(get_child_offers_client, None)
    # Reopen the same isolated test file; no application or deployed volume is touched.
    reopened = http_module.create_app(settings_module.Settings(
        token=token, child_offers_enabled=True, child_offers_repository_path=store,
    ))
    async with reopened.router.lifespan_context(reopened):
        after = ChildOffersClient("http://analytics.test", token, 5,
            transport=httpx.ASGITransport(app=reopened))
        assert (await after.history(profile))["offers"][0]["offer_id"] == offer_id
        assert (await after.evaluate(request))["replayed"] is True
