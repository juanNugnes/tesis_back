import copy
import uuid
from datetime import datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import func, select

from app.api.routes.profile_child_offers import get_child_offers_client
from app.db.models import (
    ActivityOffer,
    ActivitySelection,
    ChildOfferOperation,
    ProfileActivityPreferences,
    ProfileSettings,
)
from app.main import app
from app.repositories.activity_offer_repository import ActivityOfferRepository
from app.repositories.experimental_pmum_repository import ExperimentalPmumRepository
from app.schemas.experimental_pmum import ParentalScreenTimeResponseCreate
from app.services.automatic_opportunity_service import _inside_window
from app.services.child_offers_client import ChildOffersClient, ChildOffersError
from app.services.representative_verification_service import RepresentativeVerificationService


ZONE = ZoneInfo("America/Argentina/Buenos_Aires")


class FakeAdaptiveCoordinator:
    def __init__(self):
        self.calls = []
        self.event_calls = []
        self.receipts = {}
        self.offers = {}
        self.failure = None
        self.lose_after_create = False
        self.incomplete_content = False
        self.suppression_reason = None
        self.offer_id_override = None

    async def evaluate_adaptive(self, payload):
        self.calls.append(copy.deepcopy(payload))
        if self.failure is not None:
            error = self.failure
            self.failure = None
            raise error
        request_id = payload["request_id"]
        created = request_id not in self.receipts
        if created:
            if self.suppression_reason is not None:
                self.receipts[request_id] = {
                    "status": "suppressed",
                    "reason": self.suppression_reason,
                    "policy_version": "automatic-child-offers-v2-pmum-candidate",
                    "replayed": False,
                }
                return copy.deepcopy(self.receipts[request_id])
            offer_id = self.offer_id_override or str(uuid.uuid4())
            evidence = payload["evidence"]
            first_activity = {
                "activity_id": "breathing-bubble",
                "title": "Respirar",
                "description": "Respirá lentamente durante unos minutos.",
                "category": "quiet",
                "materials": ["Un lugar cómodo"],
                "steps": ["Sentate", "Respirá despacio"],
            }
            if self.incomplete_content:
                first_activity.pop("title")
            offer = {
                "offer_id": offer_id,
                "profile_id": evidence["profile_id"],
                "series_id": evidence["series_id"],
                "interval_id": evidence["interval_id"],
                "status": "pending",
                "expires_at": evidence["now"],
                "activities_offered": ["breathing-bubble", "draw-a-map"],
                "selection": {
                    "activities": [
                        first_activity,
                        {
                            "activity_id": "draw-a-map",
                            "title": "Dibujar",
                            "description": "Dibujá un mapa de un lugar imaginario.",
                            "category": "creative",
                        },
                    ]
                },
            }
            self.offers[offer_id] = offer
            self.receipts[request_id] = {
                "status": "offer",
                "offer": offer,
                "channel": "overlay",
                "policy_version": "automatic-child-offers-v2-pmum-candidate",
                "replayed": False,
            }
        if self.lose_after_create:
            self.lose_after_create = False
            raise ChildOffersError("CHILD_OFFERS_TIMEOUT", 504)
        return {
            **copy.deepcopy(self.receipts[request_id]),
            "replayed": not created,
        }

    async def history(self, profile_id):
        return {
            "offers": [
                copy.deepcopy(offer)
                for offer in self.offers.values()
                if offer["profile_id"] == profile_id
            ],
            "events": [],
        }

    async def event(self, payload):
        self.event_calls.append(copy.deepcopy(payload))
        offer = self.offers[payload["offer_id"]]
        if payload["event"] == "delivered":
            offer["status"] = "delivered"
        elif payload["event"] == "delivery_failed":
            offer["status"] = "failed"
        return {
            "status": "recorded",
            "offer": copy.deepcopy(offer),
            "policy_version": "automatic-child-offers-v2-pmum-candidate",
            "replayed": False,
        }


def opportunity_payload(observed_at: datetime | None = None, **overrides):
    observed = observed_at or datetime.now(ZONE) - timedelta(minutes=1)
    payload = {
        "request_id": f"opportunity-{uuid.uuid4()}",
        "series_id": f"series-{uuid.uuid4()}",
        "interval_id": f"interval-{uuid.uuid4()}",
        "observed_at": observed.isoformat(),
        "interval_started_at": (observed - timedelta(minutes=46)).isoformat(),
        "timezone": "America/Argentina/Buenos_Aires",
        "capture_verified": True,
        "screen_interactive": True,
        "game_active": False,
    }
    payload.update(overrides)
    return payload


async def configured_profile(
    client,
    db_session,
    adult_session,
    monitoring_authorization,
    *,
    configure_adult=True,
):
    observed = datetime.now(ZONE) - timedelta(minutes=1)
    school_start = (observed + timedelta(hours=2)).timetz().replace(tzinfo=None)
    school_end = (observed + timedelta(hours=3)).timetz().replace(tzinfo=None)
    sleep_start = (observed + timedelta(hours=6)).timetz().replace(tzinfo=None)
    sleep_end = (observed + timedelta(hours=14)).timetz().replace(tzinfo=None)
    created = await client.post(
        "/api/v1/profiles",
        json={
            "alias": "Adaptive child offers",
            "age": 9,
            "avatar": "fox",
            "settings": {
                "school_start": school_start.isoformat(),
                "school_end": school_end.isoformat(),
                "sleep_start": sleep_start.isoformat(),
                "sleep_end": sleep_end.isoformat(),
            },
        },
    )
    assert created.status_code == 201
    profile = created.json()
    registered = await client.post(
        f"/api/v1/profiles/{profile['id']}/devices",
        headers={"Authorization": f"Bearer {profile['bootstrap_token']}"},
        json={"installation_id": f"adaptive-{uuid.uuid4()}", "platform": "ANDROID"},
    )
    assert registered.status_code == 201
    device_headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    adult_headers, adult = await adult_session(profile["id"])
    await RepresentativeVerificationService(db_session).approve(
        adult.id,
        uuid.UUID(profile["id"]),
        verified_by="pytest-adaptive-offers",
    )
    await monitoring_authorization(profile["id"], adult=adult)
    preferences = await client.put(
        f"/api/v1/profiles/{profile['id']}/activity-preferences",
        headers=adult_headers,
        json={
            "interests": ["MUSIC"],
            "considerations_answered": True,
            "considerations": [],
            "activities_to_avoid": [],
        },
    )
    assert preferences.status_code == 200
    if configure_adult:
        configured = await client.put(
            f"/api/v1/profiles/{profile['id']}/child-offers/settings",
            headers=adult_headers,
            json={"automatic_enabled": True, "shared_phone": False},
        )
        assert configured.status_code == 200
    return profile, device_headers, adult_headers, adult, observed


async def add_parental_response(db_session, profile_id, adult, **overrides):
    from app.repositories.profile_repository import ProfileRepository

    profile = await ProfileRepository(db_session).get(uuid.UUID(profile_id))
    assert profile is not None
    values = {
        "sex_code": 1,
        "weekday_category": 2,
        "weekend_category": 3,
        "respondent": "parent_or_caregiver",
        "reference_days": 30,
    }
    values.update(overrides)
    return await ExperimentalPmumRepository(db_session).create_response(
        profile,
        adult,
        ParentalScreenTimeResponseCreate(**values),
    )


@pytest.fixture
def adaptive_coordinator(client):
    fake = FakeAdaptiveCoordinator()
    app.dependency_overrides[get_child_offers_client] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_child_offers_client, None)


@pytest.mark.asyncio
async def test_feature_flag_fails_closed(
    client, db_session, adult_session, monitoring_authorization, adaptive_coordinator,
):
    profile, device, adult_headers, adult, observed = await configured_profile(
        client, db_session, adult_session, monitoring_authorization,
    )
    await add_parental_response(db_session, profile["id"], adult)
    response = await client.post(
        f"/api/v1/profiles/{profile['id']}/child-offers/opportunities",
        headers=device,
        json=opportunity_payload(observed),
    )
    assert response.status_code == 503
    assert response.json()["detail"] == {
        "outcome": "retryable_error",
        "code": "ADAPTIVE_CHILD_OFFERS_DISABLED",
    }
    assert adaptive_coordinator.calls == []


@pytest.mark.asyncio
async def test_real_evidence_builds_v2_request_replays_and_reuses_activity_selection(
    client, db_session, adult_session, monitoring_authorization, adaptive_coordinator, monkeypatch,
):
    profile, device, _adult_headers, adult, observed = await configured_profile(
        client, db_session, adult_session, monitoring_authorization,
    )
    await add_parental_response(db_session, profile["id"], adult)
    monkeypatch.setattr(
        "app.api.routes.profile_child_offers.get_settings",
        lambda: SimpleNamespace(adaptive_child_offers_enabled=True),
    )
    payload = opportunity_payload(observed)
    url = f"/api/v1/profiles/{profile['id']}/child-offers/opportunities"
    first = await client.post(url, headers=device, json=payload)
    assert first.status_code == 200
    body = first.json()
    assert body["outcome"] == "offer_available"
    assert body["activity_offer_id"] == body["coordinator_offer_id"]
    assert body["replayed"] is False
    assert body["offer"]["selection"]["activities"] == [
        {
            "activity_id": "breathing-bubble",
            "title": "Respirar",
            "description": "Respirá lentamente durante unos minutos.",
            "category": "quiet",
            "materials": ["Un lugar cómodo"],
            "steps": ["Sentate", "Respirá despacio"],
        },
        {
            "activity_id": "draw-a-map",
            "title": "Dibujar",
            "description": "Dibujá un mapa de un lugar imaginario.",
            "category": "creative",
        },
    ]
    upstream = adaptive_coordinator.calls[0]
    assert upstream["pmum_parent_answers"] == {
        "age": 9,
        "sex_code": 1,
        "weekday_category": 2,
        "weekend_category": 3,
        "respondent": "parent_or_caregiver",
        "reference_days": 30,
    }
    assert upstream["evidence"]["assignment_verified"] is True
    assert upstream["evidence"]["shared_phone"] is False
    assert upstream["evidence"]["automatic_enabled"] is True
    assert upstream["evidence"]["game_active"] is False

    retried = await client.post(url, headers=device, json=payload)
    assert retried.status_code == 200
    assert retried.json()["replayed"] is True
    assert len(adaptive_coordinator.calls) == 1
    assert await db_session.scalar(select(func.count()).select_from(ActivityOffer)) == 1

    offers = ActivityOfferRepository(db_session)
    existing = await offers.ensure_coordinator_offer(
        offer_id=uuid.UUID(body["activity_offer_id"]),
        profile_id=uuid.UUID(profile["id"]),
        device_id=(await offers.get_offer(uuid.UUID(body["activity_offer_id"]))).device_id,
        activity_ids=["breathing-bubble", "draw-a-map"],
    )
    assert existing.id == uuid.UUID(body["activity_offer_id"])
    with pytest.raises(ValueError, match="conflicts"):
        await offers.ensure_coordinator_offer(
            offer_id=existing.id,
            profile_id=uuid.UUID(profile["id"]),
            device_id=existing.device_id,
            activity_ids=["different-activity"],
        )

    selected = await client.post(
        f"/api/v1/profiles/{profile['id']}/activity-selections",
        headers=device,
        json={
            "selection_id": str(uuid.uuid4()),
            "offer_id": body["activity_offer_id"],
            "activity_id": "breathing-bubble",
            "selected_at_epoch_millis": int(observed.timestamp() * 1000),
        },
    )
    assert selected.status_code == 200
    assert await db_session.scalar(select(func.count()).select_from(ActivitySelection)) == 1

    selected_event = await client.post(
        f"/api/v1/profiles/{profile['id']}/child-offers/opportunities/events",
        headers=device,
        json={
            "request_id": f"selected-{uuid.uuid4()}",
            "profile_id": profile["id"],
            "offer_id": body["coordinator_offer_id"],
            "event": "selected",
            "occurred_at": observed.isoformat(),
            "now": observed.isoformat(),
            "selected_activity_id": "breathing-bubble",
        },
    )
    assert selected_event.status_code == 200
    assert adaptive_coordinator.event_calls[-1]["event"] == "selected"


@pytest.mark.asyncio
async def test_delivery_is_recorded_only_after_android_confirmation(
    client, db_session, adult_session, monitoring_authorization, adaptive_coordinator, monkeypatch,
):
    profile, device, _adult_headers, adult, observed = await configured_profile(
        client, db_session, adult_session, monitoring_authorization,
    )
    await add_parental_response(db_session, profile["id"], adult)
    monkeypatch.setattr(
        "app.api.routes.profile_child_offers.get_settings",
        lambda: SimpleNamespace(adaptive_child_offers_enabled=True),
    )
    opened = await client.post(
        f"/api/v1/profiles/{profile['id']}/child-offers/opportunities",
        headers=device,
        json=opportunity_payload(observed),
    )
    assert opened.status_code == 200
    assert adaptive_coordinator.event_calls == []
    offer_id = opened.json()["coordinator_offer_id"]
    event = {
        "request_id": f"delivery-{uuid.uuid4()}",
        "profile_id": profile["id"],
        "offer_id": offer_id,
        "event": "delivered",
        "occurred_at": observed.isoformat(),
        "now": observed.isoformat(),
        "channel": opened.json()["channel"],
    }
    delivered = await client.post(
        f"/api/v1/profiles/{profile['id']}/child-offers/opportunities/events",
        headers=device,
        json=event,
    )
    assert delivered.status_code == 200
    assert delivered.json()["offer"]["status"] == "delivered"
    assert len(adaptive_coordinator.event_calls) == 1


@pytest.mark.asyncio
async def test_missing_pmum_and_unverified_capture_are_discarded_without_analytics(
    client, db_session, adult_session, monitoring_authorization, adaptive_coordinator, monkeypatch,
):
    profile, device, _adult_headers, _adult, observed = await configured_profile(
        client, db_session, adult_session, monitoring_authorization,
    )
    monkeypatch.setattr(
        "app.api.routes.profile_child_offers.get_settings",
        lambda: SimpleNamespace(adaptive_child_offers_enabled=True),
    )
    url = f"/api/v1/profiles/{profile['id']}/child-offers/opportunities"
    missing = await client.post(url, headers=device, json=opportunity_payload(observed))
    assert missing.status_code == 200
    assert missing.json()["reason"] == "pmum_answers_missing"

    capture = await client.post(
        url,
        headers=device,
        json=opportunity_payload(observed, capture_verified=False),
    )
    assert capture.status_code == 200
    assert capture.json()["reason"] == "capture_unverified"
    assert adaptive_coordinator.calls == []


@pytest.mark.asyncio
async def test_incomplete_activity_content_is_controlled_and_idempotent(
    client, db_session, adult_session, monitoring_authorization, adaptive_coordinator, monkeypatch,
):
    profile, device, _adult_headers, adult, observed = await configured_profile(
        client, db_session, adult_session, monitoring_authorization,
    )
    await add_parental_response(db_session, profile["id"], adult)
    monkeypatch.setattr(
        "app.api.routes.profile_child_offers.get_settings",
        lambda: SimpleNamespace(adaptive_child_offers_enabled=True),
    )
    adaptive_coordinator.incomplete_content = True
    payload = opportunity_payload(observed)
    url = f"/api/v1/profiles/{profile['id']}/child-offers/opportunities"

    first = await client.post(url, headers=device, json=payload)
    assert first.status_code == 200
    assert first.json() == {
        "outcome": "discarded",
        "request_id": payload["request_id"],
        "reason": "activity_content_incomplete",
        "replayed": False,
        "policy_version": "automatic-child-offers-v2-pmum-candidate",
        "coordinator_offer_id": next(iter(adaptive_coordinator.offers)),
    }
    assert await db_session.scalar(select(func.count()).select_from(ActivityOffer)) == 0
    assert [event["event"] for event in adaptive_coordinator.event_calls] == [
        "delivery_failed"
    ]

    retried = await client.post(url, headers=device, json=payload)
    assert retried.status_code == 200
    assert retried.json()["outcome"] == "discarded"
    assert retried.json()["reason"] == "activity_content_incomplete"
    assert retried.json()["replayed"] is True
    assert len(adaptive_coordinator.calls) == 1
    assert [event["event"] for event in adaptive_coordinator.event_calls] == [
        "delivery_failed"
    ]


@pytest.mark.asyncio
async def test_authorization_duration_and_missing_game_state(
    client, db_session, adult_session, monitoring_authorization, adaptive_coordinator, monkeypatch,
):
    profile, device, _adult_headers, adult, observed = await configured_profile(
        client, db_session, adult_session, monitoring_authorization,
    )
    await add_parental_response(db_session, profile["id"], adult)
    monkeypatch.setattr(
        "app.api.routes.profile_child_offers.get_settings",
        lambda: SimpleNamespace(adaptive_child_offers_enabled=True),
    )
    url = f"/api/v1/profiles/{profile['id']}/child-offers/opportunities"
    too_short = opportunity_payload(
        observed,
        interval_started_at=(observed - timedelta(minutes=44)).isoformat(),
    )
    assert (await client.post(url, headers=device, json=too_short)).status_code == 422
    missing_game = await client.post(
        url,
        headers=device,
        json=opportunity_payload(observed, game_active=None),
    )
    assert missing_game.status_code == 200
    assert missing_game.json()["reason"] == "game_state_missing"
    assert (await client.post(url, json=opportunity_payload(observed))).status_code == 401
    assert adaptive_coordinator.calls == []


@pytest.mark.asyncio
async def test_monitoring_authorization_is_required_before_coordinator(client, adaptive_coordinator):
    created = await client.post(
        "/api/v1/profiles",
        json={"alias": "No consent", "age": 9, "avatar": "fox"},
    )
    profile = created.json()
    registered = await client.post(
        f"/api/v1/profiles/{profile['id']}/devices",
        headers={"Authorization": f"Bearer {profile['bootstrap_token']}"},
        json={"installation_id": f"no-consent-{uuid.uuid4()}", "platform": "ANDROID"},
    )
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    response = await client.post(
        f"/api/v1/profiles/{profile['id']}/child-offers/opportunities",
        headers=headers,
        json=opportunity_payload(),
    )
    assert response.status_code == 403
    assert adaptive_coordinator.calls == []


@pytest.mark.asyncio
async def test_retryable_analytics_failure_keeps_exact_idempotent_payload(
    client, db_session, adult_session, monitoring_authorization, adaptive_coordinator, monkeypatch,
):
    profile, device, adult_headers, adult, observed = await configured_profile(
        client, db_session, adult_session, monitoring_authorization,
    )
    await add_parental_response(db_session, profile["id"], adult)
    monkeypatch.setattr(
        "app.api.routes.profile_child_offers.get_settings",
        lambda: SimpleNamespace(adaptive_child_offers_enabled=True),
    )
    adaptive_coordinator.lose_after_create = True
    payload = opportunity_payload(observed)
    url = f"/api/v1/profiles/{profile['id']}/child-offers/opportunities"
    failed = await client.post(url, headers=device, json=payload)
    assert failed.status_code == 504
    assert failed.json()["detail"]["outcome"] == "retryable_error"
    operation = await db_session.get(ChildOfferOperation, payload["request_id"])
    assert operation is not None and operation.status == "PENDING"

    disabled = await client.put(
        f"/api/v1/profiles/{profile['id']}/child-offers/settings",
        headers=adult_headers,
        json={"automatic_enabled": False, "shared_phone": False},
    )
    assert disabled.status_code == 200

    retried = await client.post(url, headers=device, json=payload)
    assert retried.status_code == 200
    assert retried.json()["replayed"] is True
    assert adaptive_coordinator.calls[0] == adaptive_coordinator.calls[1]
    assert len(adaptive_coordinator.offers) == 1


@pytest.mark.asyncio
async def test_adult_offer_settings_can_be_read_created_and_updated(
    client, db_session, adult_session, monitoring_authorization,
):
    profile, _device, adult_headers, _adult, _observed = await configured_profile(
        client,
        db_session,
        adult_session,
        monitoring_authorization,
        configure_adult=False,
    )
    url = f"/api/v1/profiles/{profile['id']}/child-offers/settings"
    assert (await client.get(url, headers=adult_headers)).status_code == 404

    created = await client.put(
        url,
        headers=adult_headers,
        json={"automatic_enabled": False, "shared_phone": True},
    )
    assert created.status_code == 200
    assert created.json()["automatic_enabled"] is False
    assert created.json()["shared_phone"] is True

    updated = await client.put(
        url,
        headers=adult_headers,
        json={"automatic_enabled": True, "shared_phone": False},
    )
    assert updated.status_code == 200
    assert updated.json()["automatic_enabled"] is True
    assert updated.json()["shared_phone"] is False
    assert (await client.get(url, headers=adult_headers)).json() == updated.json()


@pytest.mark.asyncio
async def test_unknown_adult_and_capture_conditions_are_discarded_before_analytics(
    client, db_session, adult_session, monitoring_authorization, adaptive_coordinator, monkeypatch,
):
    profile, device, adult_headers, _adult, observed = await configured_profile(
        client,
        db_session,
        adult_session,
        monitoring_authorization,
        configure_adult=False,
    )
    monkeypatch.setattr(
        "app.api.routes.profile_child_offers.get_settings",
        lambda: SimpleNamespace(adaptive_child_offers_enabled=True),
    )
    base = f"/api/v1/profiles/{profile['id']}"
    url = base + "/child-offers/opportunities"

    missing = await client.post(url, headers=device, json=opportunity_payload(observed))
    assert missing.json()["reason"] == "adult_offer_settings_missing"

    disabled = await client.put(
        base + "/child-offers/settings",
        headers=adult_headers,
        json={"automatic_enabled": False, "shared_phone": False},
    )
    assert disabled.status_code == 200
    response = await client.post(url, headers=device, json=opportunity_payload(observed))
    assert response.json()["reason"] == "automatic_offers_disabled"

    shared = await client.put(
        base + "/child-offers/settings",
        headers=adult_headers,
        json={"automatic_enabled": True, "shared_phone": True},
    )
    assert shared.status_code == 200
    response = await client.post(url, headers=device, json=opportunity_payload(observed))
    assert response.json()["reason"] == "shared_phone"

    enabled = await client.put(
        base + "/child-offers/settings",
        headers=adult_headers,
        json={"automatic_enabled": True, "shared_phone": False},
    )
    assert enabled.status_code == 200
    response = await client.post(
        url,
        headers=device,
        json=opportunity_payload(observed, screen_interactive=False),
    )
    assert response.json()["reason"] == "screen_inactive"
    assert adaptive_coordinator.calls == []


@pytest.mark.asyncio
async def test_schedule_eligibility_pmum_and_school_guards_use_persisted_facts(
    client, db_session, adult_session, monitoring_authorization, adaptive_coordinator, monkeypatch,
):
    profile, device, _adult_headers, adult, observed = await configured_profile(
        client, db_session, adult_session, monitoring_authorization,
    )
    monkeypatch.setattr(
        "app.api.routes.profile_child_offers.get_settings",
        lambda: SimpleNamespace(adaptive_child_offers_enabled=True),
    )
    profile_id = uuid.UUID(profile["id"])
    url = f"/api/v1/profiles/{profile['id']}/child-offers/opportunities"
    settings = await db_session.scalar(
        select(ProfileSettings).where(ProfileSettings.profile_id == profile_id)
    )
    preferences = await db_session.scalar(
        select(ProfileActivityPreferences).where(
            ProfileActivityPreferences.profile_id == profile_id
        )
    )
    assert settings is not None and preferences is not None
    original_school = (settings.school_start, settings.school_end)
    original_sleep = (settings.sleep_start, settings.sleep_end)

    settings.school_start = None
    settings.school_end = None
    await db_session.commit()
    response = await client.post(url, headers=device, json=opportunity_payload(observed))
    assert response.json()["reason"] == "school_schedule_missing"

    settings.school_start, settings.school_end = original_school
    settings.sleep_start = None
    settings.sleep_end = None
    await db_session.commit()
    response = await client.post(url, headers=device, json=opportunity_payload(observed))
    assert response.json()["reason"] == "sleep_schedule_missing"

    settings.sleep_start, settings.sleep_end = original_sleep
    preferences.considerations_answered = False
    await db_session.commit()
    response = await client.post(url, headers=device, json=opportunity_payload(observed))
    assert response.json()["reason"] == "eligibility_unknown"

    preferences.considerations_answered = True
    await db_session.commit()
    await add_parental_response(db_session, profile["id"], adult, reference_days=7)
    response = await client.post(url, headers=device, json=opportunity_payload(observed))
    assert response.json()["reason"] == "pmum_answers_ineligible"

    await add_parental_response(db_session, profile["id"], adult)
    settings.school_start = (observed - timedelta(minutes=30)).timetz().replace(tzinfo=None)
    settings.school_end = (observed + timedelta(minutes=30)).timetz().replace(tzinfo=None)
    await db_session.commit()
    response = await client.post(url, headers=device, json=opportunity_payload(observed))
    assert response.json()["reason"] == "school_period"
    assert _inside_window(time(23, 0), time(22, 0), time(7, 0)) is True
    assert adaptive_coordinator.calls == []


@pytest.mark.asyncio
async def test_coordinator_suppression_and_invalid_offer_are_controlled(
    client, db_session, adult_session, monitoring_authorization, adaptive_coordinator, monkeypatch,
):
    profile, device, _adult_headers, adult, observed = await configured_profile(
        client, db_session, adult_session, monitoring_authorization,
    )
    await add_parental_response(db_session, profile["id"], adult)
    monkeypatch.setattr(
        "app.api.routes.profile_child_offers.get_settings",
        lambda: SimpleNamespace(adaptive_child_offers_enabled=True),
    )
    url = f"/api/v1/profiles/{profile['id']}/child-offers/opportunities"

    adaptive_coordinator.suppression_reason = "outside_trigger"
    suppressed = await client.post(url, headers=device, json=opportunity_payload(observed))
    assert suppressed.status_code == 200
    assert suppressed.json()["outcome"] == "discarded"
    assert suppressed.json()["reason"] == "outside_trigger"

    adaptive_coordinator.suppression_reason = None
    adaptive_coordinator.offer_id_override = "not-a-uuid"
    invalid = await client.post(url, headers=device, json=opportunity_payload(observed))
    assert invalid.status_code == 502
    assert invalid.json()["detail"] == {
        "outcome": "definitive_error",
        "code": "INVALID_COORDINATOR_OFFER",
    }


@pytest.mark.asyncio
async def test_terminal_coordinator_error_conflicts_and_blocks_new_operation(
    client, db_session, adult_session, monitoring_authorization, adaptive_coordinator, monkeypatch,
):
    profile, device, _adult_headers, adult, observed = await configured_profile(
        client, db_session, adult_session, monitoring_authorization,
    )
    await add_parental_response(db_session, profile["id"], adult)
    monkeypatch.setattr(
        "app.api.routes.profile_child_offers.get_settings",
        lambda: SimpleNamespace(adaptive_child_offers_enabled=True),
    )
    adaptive_coordinator.failure = ChildOffersError("CHILD_OFFERS_INVALID_REQUEST", 400)
    payload = opportunity_payload(observed)
    url = f"/api/v1/profiles/{profile['id']}/child-offers/opportunities"

    failed = await client.post(url, headers=device, json=payload)
    assert failed.status_code == 400
    assert failed.json()["detail"]["outcome"] == "definitive_error"

    altered = {**payload, "screen_interactive": False}
    conflict = await client.post(url, headers=device, json=altered)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "OPPORTUNITY_CONFLICT"

    terminal = await client.post(url, headers=device, json=payload)
    assert terminal.status_code == 409
    assert terminal.json()["detail"]["code"] == "OPPORTUNITY_CONFLICT"

    pending = await client.post(url, headers=device, json=opportunity_payload(observed))
    assert pending.status_code == 409
    assert pending.json()["detail"] == {
        "outcome": "retryable_error",
        "code": "OPPORTUNITY_PENDING",
    }


@pytest.mark.asyncio
async def test_automatic_event_rejects_disabled_mismatched_and_unselected_offers(
    client, db_session, adult_session, monitoring_authorization, adaptive_coordinator, monkeypatch,
):
    profile, device, _adult_headers, adult, observed = await configured_profile(
        client, db_session, adult_session, monitoring_authorization,
    )
    await add_parental_response(db_session, profile["id"], adult)
    flag = SimpleNamespace(adaptive_child_offers_enabled=True)
    monkeypatch.setattr(
        "app.api.routes.profile_child_offers.get_settings",
        lambda: flag,
    )
    base = f"/api/v1/profiles/{profile['id']}/child-offers"
    opened = await client.post(
        base + "/opportunities",
        headers=device,
        json=opportunity_payload(observed),
    )
    assert opened.status_code == 200
    offer_id = opened.json()["coordinator_offer_id"]
    event = {
        "request_id": f"event-{uuid.uuid4()}",
        "profile_id": profile["id"],
        "offer_id": offer_id,
        "event": "delivered",
        "occurred_at": observed.isoformat(),
        "now": observed.isoformat(),
        "channel": "overlay",
    }

    flag.adaptive_child_offers_enabled = False
    disabled = await client.post(base + "/opportunities/events", headers=device, json=event)
    assert disabled.status_code == 503
    flag.adaptive_child_offers_enabled = True

    mismatch = {**event, "request_id": f"event-{uuid.uuid4()}", "profile_id": str(uuid.uuid4())}
    assert (
        await client.post(base + "/opportunities/events", headers=device, json=mismatch)
    ).status_code == 422

    invalid = {**event, "request_id": f"event-{uuid.uuid4()}", "offer_id": "not-a-uuid"}
    assert (
        await client.post(base + "/opportunities/events", headers=device, json=invalid)
    ).status_code == 404

    selected = {
        **event,
        "request_id": f"event-{uuid.uuid4()}",
        "event": "selected",
        "channel": None,
        "selected_activity_id": "breathing-bubble",
    }
    not_recorded = await client.post(
        base + "/opportunities/events",
        headers=device,
        json={key: value for key, value in selected.items() if value is not None},
    )
    assert not_recorded.status_code == 409
    assert not_recorded.json()["detail"]["code"] == "SELECTION_NOT_RECORDED"
    assert adaptive_coordinator.event_calls == []


@pytest.mark.asyncio
async def test_v2_http_client_uses_internal_token_and_exact_path():
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "status": "suppressed",
                "reason": "outside_trigger",
                "policy_version": "automatic-child-offers-v2-pmum-candidate",
                "replayed": False,
            },
        )

    profile_id = str(uuid.uuid4())
    service = ChildOffersClient(
        "http://analytics.internal",
        "private-service-token",
        2,
        transport=httpx.MockTransport(respond),
    )
    payload = {
        "request_id": "adaptive-http-test",
        "evidence": {
            "profile_id": profile_id,
            "series_id": "series-1",
            "interval_id": "interval-1",
        },
        "pmum_parent_answers": {},
    }
    result = await service.evaluate_adaptive(payload)
    assert result["status"] == "suppressed"
    assert calls[0].url.path == "/v2/child-offers/evaluate"
    assert calls[0].headers["authorization"] == "Bearer private-service-token"


@pytest.mark.asyncio
async def test_v2_http_client_preserves_catalog_content_for_backend_validation():
    profile_id = str(uuid.uuid4())
    offer_id = str(uuid.uuid4())

    def response_with(activity):
        def respond(_request):
            return httpx.Response(
                200,
                json={
                    "status": "offer",
                    "offer": {
                        "offer_id": offer_id,
                        "profile_id": profile_id,
                        "status": "pending",
                        "expires_at": datetime.now(ZONE).isoformat(),
                        "activities_offered": [activity["activity_id"]],
                        "selection": {"activities": [activity]},
                    },
                    "channel": "overlay",
                    "policy_version": "automatic-child-offers-v2-pmum-candidate",
                    "replayed": False,
                },
            )

        return respond

    payload = {
        "request_id": "adaptive-content-test",
        "evidence": {"profile_id": profile_id},
        "pmum_parent_answers": {},
    }
    complete = {
        "activity_id": "creative_favorite_01",
        "title": "Dibujá algo que te guste",
        "description": "Necesitás papel y lápices. Después hacé tu dibujo.",
        "category": "creative",
    }
    service = ChildOffersClient(
        "http://analytics.internal",
        "private-service-token",
        2,
        transport=httpx.MockTransport(response_with(complete)),
    )
    assert (await service.evaluate_adaptive(payload))["status"] == "offer"

    incomplete = {key: value for key, value in complete.items() if key != "title"}
    service = ChildOffersClient(
        "http://analytics.internal",
        "private-service-token",
        2,
        transport=httpx.MockTransport(response_with(incomplete)),
    )
    received = await service.evaluate_adaptive(payload)
    assert received["offer"]["selection"]["activities"] == [incomplete]
