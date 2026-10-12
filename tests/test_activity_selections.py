import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.api.routes.profile_activity_suggestions import (
    get_activity_selector_gateway,
)
from app.core.security import DeviceAccessTokenCodec
from app.db.models import ActivityOffer, ActivityOfferItem, ActivitySelection, Device
from app.main import app
from app.services.activity_selector_gateway import SelectorActivity

TEST_DEVICE_SECRET = "test-only-device-auth-secret-at-least-32-characters"


class FakeActivitySelectorGateway:
    def __init__(self):
        self.activities = [
            SelectorActivity(
                activity_id="creative_favorite_01",
                title="Draw something you like",
                description="Choose something you want to draw.",
                category="creative",
                activity_level="calm",
                bedtime_suitable=True,
                requires_other_person=False,
                may_require_device_interaction=False,
            ),
            SelectorActivity(
                activity_id="movement_ball_01",
                title="Play with a ball",
                description="Choose a safe way to play with a ball.",
                category="movement",
                activity_level="active",
                bedtime_suitable=False,
                requires_other_person=False,
                may_require_device_interaction=False,
            ),
        ]

    async def recommend(
        self,
        *,
        context,
        interests,
        eligibility_state,
        eligibility,
        n=3,
    ):
        return self.activities


@pytest.fixture
def fake_activity_selector(client):
    fake = FakeActivitySelectorGateway()
    app.dependency_overrides[get_activity_selector_gateway] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_activity_selector_gateway, None)


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
            "installation_id": f"activity-selection-{uuid.uuid4()}",
            "platform": "ANDROID",
        },
    )
    assert registered.status_code == 201
    device = registered.json()
    return profile, device, {"Authorization": f"Bearer {device['access_token']}"}


async def create_offer(client, profile_id, headers, context="voluntary"):
    response = await client.post(
        f"/api/v1/profiles/{profile_id}/activity-suggestions",
        headers=headers,
        json={"context": context},
    )
    assert response.status_code == 200
    return response.json()


def selection_payload(offer_id, **overrides):
    payload = {
        "selection_id": str(uuid.uuid4()),
        "offer_id": offer_id,
        "activity_id": "creative_favorite_01",
        "selected_at_epoch_millis": 1_234_567_890,
    }
    payload.update(overrides)
    return payload


async def create_selection(client, profile_id, headers, offer_id, selected_at):
    response = await client.post(
        f"/api/v1/profiles/{profile_id}/activity-selections",
        headers=headers,
        json=selection_payload(
            offer_id,
            selected_at_epoch_millis=selected_at,
        ),
    )
    assert response.status_code == 200
    return response.json()


async def count_selections(client, profile_id, headers, start, end):
    return await client.get(
        f"/api/v1/profiles/{profile_id}/activity-selections/count",
        headers=headers,
        params={
            "start_epoch_millis": start,
            "end_epoch_millis": end,
        },
    )


@pytest.mark.asyncio
async def test_activity_selection_count_is_zero_without_selections(client):
    profile, _, headers = await create_profile_and_device(client, "Count zero")

    response = await count_selections(client, profile["id"], headers, 1_000, 2_000)

    assert response.status_code == 200
    assert response.json() == {"count": 0}


@pytest.mark.asyncio
async def test_activity_selection_count_includes_one_selection(
    client,
    fake_activity_selector,
):
    profile, _, headers = await create_profile_and_device(client, "Count one")
    offer = await create_offer(client, profile["id"], headers)
    await create_selection(client, profile["id"], headers, offer["offer_id"], 1_500)

    response = await count_selections(client, profile["id"], headers, 1_000, 2_000)

    assert response.status_code == 200
    assert response.json() == {"count": 1}


@pytest.mark.asyncio
async def test_activity_selection_count_includes_multiple_selections_in_range(
    client,
    fake_activity_selector,
):
    profile, _, headers = await create_profile_and_device(client, "Count multiple")
    offer = await create_offer(client, profile["id"], headers)
    for selected_at in (1_100, 1_500, 1_999):
        await create_selection(
            client,
            profile["id"],
            headers,
            offer["offer_id"],
            selected_at,
        )

    response = await count_selections(client, profile["id"], headers, 1_000, 2_000)

    assert response.status_code == 200
    assert response.json() == {"count": 3}


@pytest.mark.asyncio
async def test_activity_selection_count_excludes_selection_before_start(
    client,
    fake_activity_selector,
):
    profile, _, headers = await create_profile_and_device(client, "Before start")
    offer = await create_offer(client, profile["id"], headers)
    await create_selection(client, profile["id"], headers, offer["offer_id"], 999)

    response = await count_selections(client, profile["id"], headers, 1_000, 2_000)

    assert response.status_code == 200
    assert response.json() == {"count": 0}


@pytest.mark.asyncio
async def test_activity_selection_count_excludes_selection_exactly_at_end(
    client,
    fake_activity_selector,
):
    profile, _, headers = await create_profile_and_device(client, "At end")
    offer = await create_offer(client, profile["id"], headers)
    await create_selection(client, profile["id"], headers, offer["offer_id"], 2_000)

    response = await count_selections(client, profile["id"], headers, 1_000, 2_000)

    assert response.status_code == 200
    assert response.json() == {"count": 0}


@pytest.mark.asyncio
async def test_activity_selection_count_includes_selection_exactly_at_start(
    client,
    fake_activity_selector,
):
    profile, _, headers = await create_profile_and_device(client, "At start")
    offer = await create_offer(client, profile["id"], headers)
    await create_selection(client, profile["id"], headers, offer["offer_id"], 1_000)

    response = await count_selections(client, profile["id"], headers, 1_000, 2_000)

    assert response.status_code == 200
    assert response.json() == {"count": 1}


@pytest.mark.asyncio
async def test_activity_selection_count_uses_selected_at_instead_of_created_at(
    client,
    db_session,
    fake_activity_selector,
):
    profile, _, headers = await create_profile_and_device(client, "Selected timestamp")
    offer = await create_offer(client, profile["id"], headers)
    selection = await create_selection(
        client,
        profile["id"],
        headers,
        offer["offer_id"],
        1_500,
    )
    stored = await db_session.get(
        ActivitySelection,
        uuid.UUID(selection["selection_id"]),
    )
    assert stored is not None
    stored.created_at = datetime(2040, 1, 1, tzinfo=UTC)
    await db_session.commit()

    response = await count_selections(client, profile["id"], headers, 1_000, 2_000)

    assert response.status_code == 200
    assert response.json() == {"count": 1}


@pytest.mark.asyncio
async def test_activity_selection_count_requires_own_device(
    client,
    adult_session,
):
    profile, _, own_headers = await create_profile_and_device(client, "Count owner")
    _, _, foreign_headers = await create_profile_and_device(client, "Count foreign")
    adult_headers, _ = await adult_session(
        profile["id"],
        email="activity-selection-count-adult@example.com",
    )

    own = await count_selections(client, profile["id"], own_headers, 1_000, 2_000)
    missing = await count_selections(client, profile["id"], {}, 1_000, 2_000)
    foreign = await count_selections(
        client,
        profile["id"],
        foreign_headers,
        1_000,
        2_000,
    )
    adult = await count_selections(client, profile["id"], adult_headers, 1_000, 2_000)

    assert own.status_code == 200
    assert missing.status_code == 401
    assert foreign.status_code == 403
    assert adult.status_code == 401


@pytest.mark.asyncio
async def test_suggestions_persist_offer_and_ordered_activity_ids(
    client,
    db_session,
    fake_activity_selector,
):
    profile, device, headers = await create_profile_and_device(client, "Offer owner")

    body = await create_offer(client, profile["id"], headers)

    assert body["offer_id"] is not None
    offer = await db_session.scalar(
        select(ActivityOffer).where(
            ActivityOffer.id == uuid.UUID(body["offer_id"])
        )
    )
    assert offer is not None
    assert str(offer.profile_id) == profile["id"]
    assert str(offer.device_id) == device["id"]
    assert offer.context == "voluntary"
    items = list(
        (
            await db_session.execute(
                select(ActivityOfferItem)
                .where(ActivityOfferItem.offer_id == offer.id)
                .order_by(ActivityOfferItem.ordinal)
            )
        ).scalars()
    )
    assert [(item.ordinal, item.activity_id) for item in items] == [
        (1, "creative_favorite_01"),
        (2, "movement_ball_01"),
    ]


@pytest.mark.asyncio
async def test_empty_suggestions_do_not_create_an_offer(
    client,
    db_session,
    fake_activity_selector,
):
    profile, _, headers = await create_profile_and_device(client, "Empty offer")
    fake_activity_selector.activities = []

    body = await create_offer(client, profile["id"], headers)

    assert body == {
        "offer_id": None,
        "context": "voluntary",
        "activities": [],
    }
    assert await db_session.scalar(select(func.count()).select_from(ActivityOffer)) == 0


@pytest.mark.asyncio
async def test_valid_selection_preserves_device_timestamp_and_backend_created_at(
    client,
    db_session,
    fake_activity_selector,
):
    profile, device, headers = await create_profile_and_device(client, "Valid selection")
    offer = await create_offer(client, profile["id"], headers)
    payload = selection_payload(offer["offer_id"])

    response = await client.post(
        f"/api/v1/profiles/{profile['id']}/activity-selections",
        headers=headers,
        json=payload,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["selection_id"] == payload["selection_id"]
    assert body["offer_id"] == offer["offer_id"]
    assert body["profile_id"] == profile["id"]
    assert body["device_id"] == device["id"]
    assert body["activity_id"] == payload["activity_id"]
    assert body["selected_at_epoch_millis"] == 1_234_567_890
    assert body["created_at"] is not None
    stored = await db_session.get(ActivitySelection, uuid.UUID(payload["selection_id"]))
    assert stored is not None
    assert stored.selected_at_epoch_millis == 1_234_567_890


@pytest.mark.asyncio
async def test_selection_retry_with_identical_payload_is_idempotent(
    client,
    db_session,
    fake_activity_selector,
):
    profile, _, headers = await create_profile_and_device(client, "Idempotent")
    offer = await create_offer(client, profile["id"], headers)
    payload = selection_payload(offer["offer_id"])
    url = f"/api/v1/profiles/{profile['id']}/activity-selections"

    first = await client.post(url, headers=headers, json=payload)
    second = await client.post(url, headers=headers, json=payload)
    counted = await count_selections(
        client,
        profile["id"],
        headers,
        1_234_567_890,
        1_234_567_891,
    )

    assert first.status_code == second.status_code == 200
    assert second.json() == first.json()
    assert counted.status_code == 200
    assert counted.json() == {"count": 1}
    assert await db_session.scalar(
        select(func.count()).select_from(ActivitySelection)
    ) == 1


@pytest.mark.asyncio
async def test_reused_selection_id_with_different_payload_is_conflict(
    client,
    db_session,
    fake_activity_selector,
):
    profile, _, headers = await create_profile_and_device(client, "Conflict")
    offer = await create_offer(client, profile["id"], headers)
    payload = selection_payload(offer["offer_id"])
    url = f"/api/v1/profiles/{profile['id']}/activity-selections"
    first = await client.post(url, headers=headers, json=payload)

    conflict = await client.post(
        url,
        headers=headers,
        json={**payload, "selected_at_epoch_millis": 1_234_567_891},
    )

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert await db_session.scalar(
        select(func.count()).select_from(ActivitySelection)
    ) == 1


@pytest.mark.asyncio
async def test_activity_not_present_in_offer_is_rejected(
    client,
    db_session,
    fake_activity_selector,
):
    profile, _, headers = await create_profile_and_device(client, "Not offered")
    offer = await create_offer(client, profile["id"], headers)
    payload = selection_payload(
        offer["offer_id"],
        activity_id="activity_not_in_offer",
    )

    response = await client.post(
        f"/api/v1/profiles/{profile['id']}/activity-selections",
        headers=headers,
        json=payload,
    )

    assert response.status_code == 422
    assert await db_session.scalar(
        select(func.count()).select_from(ActivitySelection)
    ) == 0


@pytest.mark.asyncio
async def test_offer_from_another_profile_and_device_is_rejected(
    client,
    db_session,
    fake_activity_selector,
):
    first, _, first_headers = await create_profile_and_device(client, "Offer A")
    second, _, second_headers = await create_profile_and_device(client, "Offer B")
    first_offer = await create_offer(client, first["id"], first_headers)
    payload = selection_payload(first_offer["offer_id"])

    response = await client.post(
        f"/api/v1/profiles/{second['id']}/activity-selections",
        headers=second_headers,
        json=payload,
    )

    assert response.status_code == 403
    assert await db_session.scalar(
        select(func.count()).select_from(ActivitySelection)
    ) == 0


@pytest.mark.asyncio
async def test_offer_from_another_device_on_same_profile_is_rejected(
    client,
    db_session,
    fake_activity_selector,
):
    profile, _, first_headers = await create_profile_and_device(client, "Two devices")
    offer = await create_offer(client, profile["id"], first_headers)
    second_device = Device(
        profile_id=uuid.UUID(profile["id"]),
        installation_id=f"second-device-{uuid.uuid4()}",
        platform="ANDROID",
        active=True,
    )
    db_session.add(second_device)
    await db_session.commit()
    await db_session.refresh(second_device)
    token, _ = DeviceAccessTokenCodec(TEST_DEVICE_SECRET, 60).issue(second_device.id)

    response = await client.post(
        f"/api/v1/profiles/{profile['id']}/activity-selections",
        headers={"Authorization": f"Bearer {token}"},
        json=selection_payload(offer["offer_id"]),
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_activity_selection_requires_own_device(
    client,
    adult_session,
    fake_activity_selector,
):
    profile, _, own_headers = await create_profile_and_device(client, "Selection owner")
    _, _, foreign_headers = await create_profile_and_device(client, "Selection foreign")
    adult_headers, _ = await adult_session(
        profile["id"],
        email="activity-selection-adult@example.com",
    )
    offer = await create_offer(client, profile["id"], own_headers)
    payload = selection_payload(offer["offer_id"])
    url = f"/api/v1/profiles/{profile['id']}/activity-selections"

    missing = await client.post(url, json=payload)
    foreign = await client.post(url, headers=foreign_headers, json=payload)
    adult = await client.post(url, headers=adult_headers, json=payload)

    assert missing.status_code == 401
    assert foreign.status_code == 403
    assert adult.status_code == 401


@pytest.mark.asyncio
async def test_empty_offer_response_cannot_be_used_for_a_selection(
    client,
    fake_activity_selector,
):
    profile, _, headers = await create_profile_and_device(client, "No offer")
    fake_activity_selector.activities = []
    offer = await create_offer(client, profile["id"], headers)
    assert offer["offer_id"] is None

    response = await client.post(
        f"/api/v1/profiles/{profile['id']}/activity-selections",
        headers=headers,
        json=selection_payload(str(uuid.uuid4())),
    )

    assert response.status_code == 404


def test_activity_selection_openapi_contract_is_device_only():
    paths = app.openapi()["paths"]

    assert paths["/api/v1/profiles/{profile_id}/activity-suggestions"]["post"][
        "security"
    ] == [{"DeviceBearer": []}]
    assert paths["/api/v1/profiles/{profile_id}/activity-selections"]["post"][
        "security"
    ] == [{"DeviceBearer": []}]
    assert paths["/api/v1/profiles/{profile_id}/activity-selections/count"]["get"][
        "security"
    ] == [{"DeviceBearer": []}]
