import uuid
from datetime import date, timedelta

import pytest

from app.api.routes.profile_activity_suggestions import (
    get_activity_selector_gateway,
)
from app.db.models import ActivityInterest
from app.main import app
from app.schemas.profile_activity_preferences import ProfileActivityPreferencesRead
from app.services.activity_selector_gateway import (
    ActivityEligibility,
    ActivitySelectorTechnicalError,
    SelectorActivity,
)
from app.services.activity_selector_mapper import (
    ActivityEligibilityMapper,
    ActivityInterestMapper,
)


class FakeActivitySelectorGateway:
    def __init__(self):
        self.activities = []
        self.error = None
        self.calls = []

    async def recommend(self, *, context, interests, eligibility, n=3):
        self.calls.append(
            {
                "context": context,
                "interests": interests,
                "eligibility": eligibility,
                "n": n,
            }
        )
        if self.error is not None:
            raise self.error
        return self.activities


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
        interest_tags=("drawing",),
        allowed_contexts=("voluntary",),
        activity_level="calm",
        bedtime_suitable=index % 2 == 0,
        requires_other_person=False,
        may_require_device_interaction=False,
    )


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


@pytest.mark.parametrize(
    ("adaptation_type", "valid_until", "expected_active"),
    [
        ("NONE", date(2027, 1, 1), False),
        ("TEMPORARY", None, False),
        ("TEMPORARY", date(2026, 10, 9), True),
        ("TEMPORARY", date(2026, 10, 8), False),
        ("PERMANENT", None, True),
    ],
)
def test_adaptation_activity_is_evaluated_in_backend(
    adaptation_type,
    valid_until,
    expected_active,
):
    mapped = ActivityEligibilityMapper.map(
        preferences(
            adaptation_type=adaptation_type,
            valid_until=valid_until,
        ),
        today=date(2026, 10, 9),
    )

    assert mapped.adaptation_active is expected_active
    assert mapped.eligibility == ActivityEligibility()


def test_considerations_do_not_create_unagreed_eligibility_filters():
    mapped = ActivityEligibilityMapper.map(
        preferences(considerations=["MOBILITY", "SENSORY_SENSITIVITY"]),
        today=date(2026, 10, 9),
    )

    assert mapped.eligibility == ActivityEligibility()


def test_activities_to_avoid_do_not_create_unagreed_eligibility_filters():
    mapped = ActivityEligibilityMapper.map(
        preferences(activities_to_avoid=["RUNNING", "LOUD_SOUNDS"]),
        today=date(2026, 10, 9),
    )

    assert mapped.eligibility == ActivityEligibility()


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
            "adaptation_type": "TEMPORARY",
            "considerations": ["MOBILITY"],
            "activities_to_avoid": ["RUNNING"],
            "additional_notes": "Do not interpret this free text",
            "valid_until": (date.today() + timedelta(days=1)).isoformat(),
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
            "eligibility": ActivityEligibility(),
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


@pytest.mark.asyncio
async def test_unconfigured_runtime_gateway_returns_controlled_technical_failure(client):
    profile, headers = await create_profile_and_device(client)

    response = await client.post(
        f"/api/v1/profiles/{profile['id']}/activity-suggestions",
        headers=headers,
        json={"context": "voluntary"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "SELECTOR_NOT_CONFIGURED"


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
    operation = app.openapi()["paths"][
        "/api/v1/profiles/{profile_id}/activity-suggestions"
    ]["post"]

    assert operation["security"] == [{"DeviceBearer": []}]
