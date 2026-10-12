import json
import uuid
from dataclasses import asdict
from datetime import date, timedelta
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from app.api.routes.profile_activity_suggestions import (
    get_activity_selector_gateway,
)
from app.db.models import ActivityInterest
from app.main import app
from app.schemas.profile_activity_preferences import ProfileActivityPreferencesRead
from app.services.activity_selector_gateway import (
    ActivityEligibility,
    ActivitySelectorResult,
    ActivitySelectorTechnicalError,
    SelectorActivity,
)
from app.services.activity_selector_http_gateway import HttpActivitySelectorGateway
from app.services.activity_selector_mapper import (
    ActivityEligibilityMapper,
    ActivityInterestMapper,
)


class FakeActivitySelectorGateway:
    def __init__(self):
        self.activities = []
        self.reason = None
        self.error = None
        self.calls = []

    async def recommend(
        self,
        *,
        context,
        interests,
        eligibility_state,
        eligibility,
        n=3,
    ):
        self.calls.append(
            {
                "context": context,
                "interests": interests,
                "eligibility_state": eligibility_state,
                "eligibility": eligibility,
                "n": n,
            }
        )
        if self.error is not None:
            raise self.error
        return ActivitySelectorResult(
            activities=tuple(self.activities),
            reason=self.reason,
        )


@pytest.fixture
def fake_activity_selector(client):
    fake = FakeActivitySelectorGateway()
    app.dependency_overrides[get_activity_selector_gateway] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_activity_selector_gateway, None)


async def create_profile_and_device(client, alias="Activity suggestions"):
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
            "installation_id": f"activity-suggestions-{uuid.uuid4()}",
            "platform": "ANDROID",
        },
    )
    assert registered.status_code == 201
    return profile, {"Authorization": f"Bearer {registered.json()['access_token']}"}


def selector_activity(index):
    return SelectorActivity(
        activity_id=f"activity-{index}",
        title=f"Activity {index}",
        description=f"Description {index}",
        category="creative",
        activity_level="calm",
        bedtime_suitable=index % 2 == 0,
        requires_other_person=False,
        may_require_device_interaction=False,
    )


def analytics_activity(activity):
    return {
        **asdict(activity),
        "interest_tags": ["creative"],
        "allowed_contexts": ["voluntary", "general"],
    }


def analytics_selection_response(request_payload, activities):
    status = "selected" if activities else "no_eligible_activities"
    return {
        "status": status,
        "reason": None if activities else "no_eligible_activities",
        "request_id": request_payload["request_id"],
        "selection_fingerprint": "selection-fingerprint",
        "catalog_version": "provisional-sha256:catalog",
        "catalog_status": "PROVISIONAL",
        "selector_version": "published-selector-sha256:selector",
        "activities": [analytics_activity(activity) for activity in activities],
    }


def preferences(**overrides):
    values = {"profile_id": uuid.uuid4()}
    values.update(overrides)
    return ProfileActivityPreferencesRead(**values)


@pytest.mark.parametrize(
    ("interest", "expected_tags", "expected_unmapped"),
    [
        (ActivityInterest.MUSIC, ("music",), ()),
        (ActivityInterest.MOVEMENT, ("movement",), ()),
        (ActivityInterest.BUILDING, ("building",), ()),
        (ActivityInterest.WRITING, ("writing",), ()),
        (
            ActivityInterest.DRAWING_CREATING,
            (),
            (ActivityInterest.DRAWING_CREATING,),
        ),
        (
            ActivityInterest.READING_STORIES,
            (),
            (ActivityInterest.READING_STORIES,),
        ),
        (
            ActivityInterest.GAMES_CHALLENGES,
            (),
            (ActivityInterest.GAMES_CHALLENGES,),
        ),
        (ActivityInterest.OUTDOORS, (), (ActivityInterest.OUTDOORS,)),
        (
            ActivityInterest.IMAGINATIVE_PLAY,
            (),
            (ActivityInterest.IMAGINATIVE_PLAY,),
        ),
    ],
)
def test_interest_mapping_is_explicit_and_conservative(
    interest,
    expected_tags,
    expected_unmapped,
):
    mapped = ActivityInterestMapper.map([interest])

    assert mapped.selector_tags == expected_tags
    assert mapped.unmapped == expected_unmapped


def test_interest_mapping_combines_only_verified_tags():
    mapped = ActivityInterestMapper.map(
        [ActivityInterest.MUSIC, ActivityInterest.MOVEMENT]
    )

    assert mapped.selector_tags == ("music", "movement")
    assert mapped.unmapped == ()


def test_interest_mapping_without_known_tags_is_explicitly_empty():
    mapped = ActivityInterestMapper.map(
        [ActivityInterest.DRAWING_CREATING, ActivityInterest.OUTDOORS]
    )

    assert mapped.selector_tags == ()
    assert mapped.unmapped == (
        ActivityInterest.DRAWING_CREATING,
        ActivityInterest.OUTDOORS,
    )


def test_omitted_considerations_map_to_unknown_without_eligibility():
    mapped = ActivityEligibilityMapper.map(preferences())

    assert mapped.eligibility_state == "unknown"
    assert mapped.eligibility is None


def test_explicit_no_considerations_maps_to_known_empty_eligibility():
    mapped = ActivityEligibilityMapper.map(
        preferences(considerations_answered=True, considerations=[])
    )

    assert mapped.eligibility_state == "known"
    assert mapped.eligibility == ActivityEligibility()


def test_considerations_do_not_create_unagreed_eligibility_filters():
    mapped = ActivityEligibilityMapper.map(
        preferences(
            considerations_answered=True,
            considerations=[
                {
                    "type": "UPPER_BODY_MOBILITY",
                    "duration_type": "PERMANENT",
                    "valid_until": None,
                },
                {
                    "type": "SOUND_SENSITIVITY",
                    "duration_type": "PERMANENT",
                    "valid_until": None,
                },
            ],
        ),
    )

    assert mapped.eligibility_state == "unknown"
    assert mapped.eligibility is None


def test_activities_to_avoid_do_not_create_unagreed_eligibility_filters():
    mapped = ActivityEligibilityMapper.map(
        preferences(activities_to_avoid=["RUNNING", "LOUD_SOUNDS"]),
        today=date(2026, 10, 9),
    )

    assert mapped.eligibility_state == "unknown"
    assert mapped.eligibility is None


@pytest.mark.asyncio
async def test_persisted_preferences_are_mapped_without_sending_notes(
    client,
    fake_activity_selector,
):
    profile, headers = await create_profile_and_device(client)
    updated = await client.put(
        f"/api/v1/profiles/{profile['id']}/activity-preferences",
        headers=headers,
        json={
            "interests": ["MUSIC", "MOVEMENT", "DRAWING_CREATING"],
            "considerations_answered": True,
            "considerations": [
                {
                    "type": "LOWER_BODY_MOBILITY",
                    "duration_type": "TEMPORARY",
                    "valid_until": (date.today() + timedelta(days=1)).isoformat(),
                },
                {
                    "type": "SOUND_SENSITIVITY",
                    "duration_type": "PERMANENT",
                    "valid_until": None,
                },
            ],
            "activities_to_avoid": ["RUNNING"],
            "additional_notes": "Do not interpret this free text",
        },
    )
    assert updated.status_code == 200

    response = await client.post(
        f"/api/v1/profiles/{profile['id']}/activity-suggestions",
        headers=headers,
        json={"context": "voluntary"},
    )

    assert response.status_code == 200
    assert fake_activity_selector.calls == [
        {
            "context": "voluntary",
            "interests": ("music", "movement"),
            "eligibility_state": "unknown",
            "eligibility": None,
            "n": 3,
        }
    ]


@pytest.mark.asyncio
async def test_no_mappable_interests_are_sent_as_an_empty_tuple(
    client,
    fake_activity_selector,
):
    profile, headers = await create_profile_and_device(client)
    updated = await client.put(
        f"/api/v1/profiles/{profile['id']}/activity-preferences",
        headers=headers,
        json={"interests": ["READING_STORIES", "IMAGINATIVE_PLAY"]},
    )
    assert updated.status_code == 200

    response = await client.post(
        f"/api/v1/profiles/{profile['id']}/activity-suggestions",
        headers=headers,
        json={"context": "voluntary"},
    )

    assert response.status_code == 200
    assert fake_activity_selector.calls[0]["interests"] == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("activity_count", [3, 1, 0])
async def test_selector_cardinality_is_returned_as_a_valid_response(
    client,
    fake_activity_selector,
    activity_count,
):
    profile, headers = await create_profile_and_device(client)
    fake_activity_selector.activities = [
        selector_activity(index) for index in range(activity_count)
    ]

    response = await client.post(
        f"/api/v1/profiles/{profile['id']}/activity-suggestions",
        headers=headers,
        json={"context": "voluntary"},
    )

    assert response.status_code == 200
    assert len(response.json()["activities"]) == activity_count
    assert response.json()["reason"] is None
    if activity_count:
        assert set(response.json()["activities"][0]) == {
            "activity_id",
            "title",
            "description",
            "category",
            "activity_level",
            "bedtime_suitable",
            "requires_other_person",
            "may_require_device_interaction",
        }


@pytest.mark.asyncio
async def test_service_never_exposes_more_than_three_activities(
    client,
    fake_activity_selector,
):
    profile, headers = await create_profile_and_device(client)
    fake_activity_selector.activities = [selector_activity(index) for index in range(4)]

    response = await client.post(
        f"/api/v1/profiles/{profile['id']}/activity-suggestions",
        headers=headers,
        json={"context": "voluntary"},
    )

    assert response.status_code == 200
    assert len(response.json()["activities"]) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("context", ["voluntary", "bedtime"])
async def test_supported_context_is_forwarded_to_selector(
    client,
    fake_activity_selector,
    context,
):
    profile, headers = await create_profile_and_device(client)

    response = await client.post(
        f"/api/v1/profiles/{profile['id']}/activity-suggestions",
        headers=headers,
        json={"context": context},
    )

    assert response.status_code == 200
    assert response.json()["context"] == context
    assert fake_activity_selector.calls[0]["context"] == context


@pytest.mark.asyncio
@pytest.mark.parametrize("context", ["general", "unknown"])
async def test_unsupported_context_is_rejected(
    client,
    fake_activity_selector,
    context,
):
    profile, headers = await create_profile_and_device(client)

    response = await client.post(
        f"/api/v1/profiles/{profile['id']}/activity-suggestions",
        headers=headers,
        json={"context": context},
    )

    assert response.status_code == 422
    assert fake_activity_selector.calls == []


@pytest.mark.asyncio
async def test_gateway_technical_failure_is_not_reported_as_zero_activities(
    client,
    fake_activity_selector,
):
    profile, headers = await create_profile_and_device(client)
    fake_activity_selector.error = ActivitySelectorTechnicalError(
        "SELECTOR_TIMEOUT",
        "Activity selector timed out",
    )

    response = await client.post(
        f"/api/v1/profiles/{profile['id']}/activity-suggestions",
        headers=headers,
        json={"context": "voluntary"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "SELECTOR_TIMEOUT",
            "message": "Activity selector timed out",
        }
    }


def test_runtime_dependency_builds_configured_http_gateway(monkeypatch):
    monkeypatch.setattr(
        "app.api.routes.profile_activity_suggestions.get_settings",
        lambda: SimpleNamespace(
            analytics_service_url="http://analytics.test/",
            analytics_service_token=SecretStr("analytics-service-token"),
            analytics_timeout_seconds=2.5,
        ),
    )

    gateway = get_activity_selector_gateway()

    assert isinstance(gateway, HttpActivitySelectorGateway)
    assert gateway.endpoint == "http://analytics.test/v1/activities/select"
    assert gateway.service_token == "analytics-service-token"
    assert gateway.timeout_seconds == 2.5


@pytest.mark.asyncio
async def test_activity_suggestions_require_own_device(
    client,
    adult_session,
    fake_activity_selector,
):
    profile, own_headers = await create_profile_and_device(client, "Suggestion owner")
    _, foreign_headers = await create_profile_and_device(client, "Suggestion foreign")
    adult_headers, _ = await adult_session(
        profile["id"],
        email="activity-suggestions-adult@example.com",
    )
    url = f"/api/v1/profiles/{profile['id']}/activity-suggestions"

    missing = await client.post(url, json={"context": "voluntary"})
    foreign = await client.post(
        url,
        headers=foreign_headers,
        json={"context": "voluntary"},
    )
    adult = await client.post(
        url,
        headers=adult_headers,
        json={"context": "voluntary"},
    )
    own = await client.post(
        url,
        headers=own_headers,
        json={"context": "voluntary"},
    )

    assert missing.status_code == 401
    assert foreign.status_code == 403
    assert adult.status_code == 401
    assert own.status_code == 200
    assert len(fake_activity_selector.calls) == 1


def test_activity_suggestions_openapi_contract_is_device_only():
    openapi = app.openapi()
    operation = openapi["paths"][
        "/api/v1/profiles/{profile_id}/activity-suggestions"
    ]["post"]

    assert operation["security"] == [{"DeviceBearer": []}]
    response_schema = openapi["components"]["schemas"]["ActivitySuggestionsRead"]
    reason_schema = response_schema["properties"]["reason"]
    assert {item.get("type") for item in reason_schema["anyOf"]} == {
        "string",
        "null",
    }
    assert "reason" not in response_schema.get("required", [])


@pytest.mark.asyncio
@pytest.mark.parametrize("activity_count", [3, 1, 0])
async def test_http_gateway_sends_exact_payload_and_accepts_valid_cardinalities(
    activity_count,
):
    captured = {"payload": None, "authorization": None, "content_type": None}
    activities = [selector_activity(index) for index in range(activity_count)]

    async def handler(request):
        request_payload = json.loads(request.content)
        captured["payload"] = request_payload
        captured["authorization"] = request.headers.get("Authorization")
        captured["content_type"] = request.headers.get("Content-Type")
        return httpx.Response(
            200,
            request=request,
            json=analytics_selection_response(request_payload, activities),
        )

    gateway = HttpActivitySelectorGateway(
        "http://analytics.test/",
        "analytics-service-token",
        5,
        transport=httpx.MockTransport(handler),
    )
    result = await gateway.recommend(
        context="voluntary",
        interests=("music", "movement"),
        eligibility_state="known",
        eligibility=ActivityEligibility(),
        n=3,
    )

    assert len(result.activities) == activity_count
    assert result.reason == (None if activities else "no_eligible_activities")
    request_id = captured["payload"].pop("request_id")
    assert request_id.startswith("modera-")
    assert captured["payload"] == {
        "context": "voluntary",
        "interests": ["music", "movement"],
        "eligibility_state": "known",
        "eligibility": {},
        "recently_shown_ids": [],
    }
    assert captured["authorization"] == "Bearer analytics-service-token"
    assert captured["content_type"] == "application/json"


@pytest.mark.asyncio
async def test_http_gateway_unknown_eligibility_omits_filters_and_accepts_unavailable():
    captured = {}

    async def handler(request):
        request_payload = json.loads(request.content)
        captured.update(request_payload)
        return httpx.Response(
            200,
            request=request,
            json={
                "status": "unavailable",
                "reason": "eligibility_unknown",
                "request_id": request_payload["request_id"],
                "catalog_version": "provisional-sha256:catalog",
                "catalog_status": "PROVISIONAL",
                "selector_version": "published-selector-sha256:selector",
                "activities": [],
            },
        )

    gateway = HttpActivitySelectorGateway(
        "http://analytics.test",
        "analytics-service-token",
        5,
        transport=httpx.MockTransport(handler),
    )

    result = await gateway.recommend(
        context="voluntary",
        interests=(),
        eligibility_state="unknown",
        eligibility=None,
    )

    assert result.activities == ()
    assert result.reason == "eligibility_unknown"
    assert captured["eligibility_state"] == "unknown"
    assert "eligibility" not in captured


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("analytics_status", "analytics_reason", "activity_count"),
    [
        pytest.param(
            "unavailable",
            "eligibility_unknown",
            0,
            id="eligibility-unknown",
        ),
        pytest.param(
            "no_eligible_activities",
            "no_eligible_activities",
            0,
            id="no-eligible-activities",
        ),
        pytest.param("selected", None, 1, id="selected"),
        pytest.param(
            "unavailable",
            "future_functional_reason",
            0,
            id="unknown-reason",
        ),
    ],
)
async def test_analytics_reason_is_propagated_to_successful_backend_response(
    client,
    analytics_status,
    analytics_reason,
    activity_count,
):
    profile, headers = await create_profile_and_device(
        client,
        f"Analytics {analytics_status}",
    )
    activities = [selector_activity(index) for index in range(activity_count)]

    async def handler(request):
        request_payload = json.loads(request.content)
        return httpx.Response(
            200,
            request=request,
            json={
                "status": analytics_status,
                "reason": analytics_reason,
                "request_id": request_payload["request_id"],
                "selection_fingerprint": (
                    "selection-fingerprint"
                    if analytics_status != "unavailable"
                    else None
                ),
                "catalog_version": "provisional-sha256:catalog",
                "catalog_status": "PROVISIONAL",
                "selector_version": "published-selector-sha256:selector",
                "activities": [
                    analytics_activity(activity) for activity in activities
                ],
            },
        )

    gateway = HttpActivitySelectorGateway(
        "http://analytics.test",
        "analytics-service-token",
        5,
        transport=httpx.MockTransport(handler),
    )
    app.dependency_overrides[get_activity_selector_gateway] = lambda: gateway
    try:
        response = await client.post(
            f"/api/v1/profiles/{profile['id']}/activity-suggestions",
            headers=headers,
            json={"context": "voluntary"},
        )
    finally:
        app.dependency_overrides.pop(get_activity_selector_gateway, None)

    assert response.status_code == 200
    body = response.json()
    assert body["reason"] == analytics_reason
    assert len(body["activities"]) == activity_count
    assert (body["offer_id"] is not None) is (activity_count > 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "expected_code"),
    [
        ("timeout", "SELECTOR_TIMEOUT"),
        ("connection", "SELECTOR_CONNECTION_ERROR"),
        ("http", "SELECTOR_HTTP_ERROR"),
        ("invalid", "SELECTOR_INVALID_RESPONSE"),
    ],
)
async def test_http_gateway_classifies_technical_failures(scenario, expected_code):
    async def handler(request):
        if scenario == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        if scenario == "connection":
            raise httpx.ConnectError("connection refused", request=request)
        if scenario == "http":
            return httpx.Response(500, request=request)
        return httpx.Response(200, request=request, json={"unexpected": []})

    gateway = HttpActivitySelectorGateway(
        "http://analytics.test",
        "analytics-service-token",
        1,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ActivitySelectorTechnicalError) as exc_info:
        await gateway.recommend(
            context="bedtime",
            interests=(),
            eligibility_state="known",
            eligibility=ActivityEligibility(),
            n=3,
        )

    assert exc_info.value.code == expected_code
