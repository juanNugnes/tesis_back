import asyncio
from datetime import date, datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import select

from app.db.models import UsageSnapshot
from app.schemas.usage import DailyUsageCreate
from app.services.usage_service import UsageService


async def create_profile(client, payload=None):
    response = await client.post(
        "/api/v1/profiles",
        json=payload or {"alias": "Perfil A", "age": 9, "avatar": "fox"},
    )
    assert response.status_code == 201
    profile = response.json()
    registered = await client.post(
        f"/api/v1/profiles/{profile['id']}/devices",
        headers={"Authorization": f"Bearer {profile['bootstrap_token']}"},
        json={"installation_id": f"test-{uuid.uuid4()}", "platform": "ANDROID"},
    )
    assert registered.status_code == 201
    device_headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    link_code = await client.post(
        f"/api/v1/profiles/{profile['id']}/adult-link-codes",
        headers=device_headers,
    )
    assert link_code.status_code == 201
    if "Authorization" in client.headers:
        adult_headers = {"Authorization": client.headers["Authorization"]}
    else:
        email = "functional-tests@example.com"
        requested = await client.post("/api/v1/auth/adult/otp/request", json={"email": email})
        verified = await client.post(
            "/api/v1/auth/adult/otp/verify",
            json={"email": email, "code": requested.json()["dev_code"]},
        )
        adult_headers = {"Authorization": f"Bearer {verified.json()['access_token']}"}
    claimed = await client.post(
        "/api/v1/adult/profile-links/claim",
        headers=adult_headers,
        json={"code": link_code.json()["code"]},
    )
    assert claimed.status_code == 200
    client.headers.update(adult_headers)
    profile["_device_headers"] = device_headers
    return profile


async def create_context(client, profile_id, **overrides):
    payload = {
        "type": "VACATION",
        "start_date": "2026-09-01",
        "end_date": "2026-09-07",
        "description": None,
    }
    payload.update(overrides)
    response = await client.post(f"/api/v1/profiles/{profile_id}/contexts", json=payload)
    assert response.status_code == 201
    return response.json()


def usage_payload(**overrides):
    payload = {
        "snapshot_id": "00000000-0000-0000-0000-000000000815",
        "captured_at": "2026-08-15T20:00:00Z",
        "date": "2026-08-15",
        "period_start_at": "2026-08-15T00:00:00Z",
        "period_end_at": "2026-08-15T19:00:00Z",
        "timezone": "UTC",
        "duration_unit": "MINUTES",
        "measurement_method": "ANDROID_USAGE_STATS_AGGREGATES",
        "calculation_version": "1.0",
        "coverage_status": "UNKNOWN",
        "total_usage_minutes": 230,
        "session_count": 25,
        "night_usage_minutes": 45,
        "max_session_minutes": 60,
        "unlock_count": 35,
        "applications": [{"package_name": "com.example.app", "app_name": "Ejemplo", "category": "SOCIAL", "usage_minutes": 80}],
        "metric_statuses": {
            "total_usage_minutes": "MEASURED",
            "session_count": "MEASURED",
            "night_usage_minutes": "MEASURED",
            "max_session_minutes": "MEASURED",
            "unlock_count": "MEASURED",
            "applications": "MEASURED",
        },
        "night_window": {
            "source": "PROFILE_SLEEP_SCHEDULE",
            "start_local_time": "22:00:00",
            "end_local_time": "07:00:00",
        },
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_create_profile(client):
    profile = await create_profile(client)
    assert profile["alias"] == "Perfil A"
    assert profile["avatar"] == "fox"
    assert profile["active"] is True
    response = await client.get(f"/api/v1/profiles/{profile['id']}")
    assert response.status_code == 200
    assert response.json()["settings"]["school_start"] is None
    assert response.json()["settings"]["sleep_start"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("age", [5, 13])
async def test_reject_age_outside_range(client, age):
    response = await client.post("/api/v1/profiles", json={"alias": "Inválido", "age": age, "avatar": "fox"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_reject_invalid_avatar(client):
    response = await client.post("/api/v1/profiles", json={"alias": "Perfil", "age": 9, "avatar": "bear"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_update_avatar(client):
    profile = await create_profile(client)
    response = await client.put(f"/api/v1/profiles/{profile['id']}", json={"avatar": "koala"})
    assert response.status_code == 200
    assert response.json()["avatar"] == "koala"
    assert response.json()["settings"]["profile_id"] == profile["id"]


@pytest.mark.asyncio
async def test_update_alias(client):
    profile = await create_profile(client)
    response = await client.put(f"/api/v1/profiles/{profile['id']}", json={"alias": "Perfil B"})
    assert response.status_code == 200
    assert response.json()["alias"] == "Perfil B"


@pytest.mark.asyncio
async def test_update_age(client):
    profile = await create_profile(client)
    response = await client.put(f"/api/v1/profiles/{profile['id']}", json={"age": 10})
    assert response.status_code == 200
    assert response.json()["age"] == 10


@pytest.mark.asyncio
async def test_reject_update_active(client):
    profile = await create_profile(client)
    response = await client.put(f"/api/v1/profiles/{profile['id']}", json={"active": False})
    assert response.status_code == 422
    assert (await client.get(f"/api/v1/profiles/{profile['id']}")).json()["active"] is True


@pytest.mark.asyncio
async def test_update_multiple_profile_fields(client):
    profile = await create_profile(client)
    response = await client.put(
        f"/api/v1/profiles/{profile['id']}",
        json={"alias": "Perfil C", "age": 11, "avatar": "lion"},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["alias"] == "Perfil C"
    assert result["age"] == 11
    assert result["avatar"] == "lion"
    assert result["active"] is True


@pytest.mark.asyncio
async def test_update_profile_returns_detail_and_preserves_settings(client):
    profile = await create_profile(client)
    settings_url = f"/api/v1/profiles/{profile['id']}/settings"
    settings_payload = {
        "school_start": "08:00",
        "school_end": "12:30",
        "sleep_start": "22:00",
        "sleep_end": "07:00",
    }
    settings_response = await client.put(settings_url, json=settings_payload)
    assert settings_response.status_code == 200

    response = await client.put(f"/api/v1/profiles/{profile['id']}", json={"alias": "Perfil actualizado"})
    assert response.status_code == 200
    result = response.json()
    assert set(result) == {"id", "alias", "age", "avatar", "active", "created_at", "updated_at", "settings"}
    assert result["settings"] == settings_response.json()
    assert (await client.get(settings_url)).json() == settings_response.json()


@pytest.mark.asyncio
async def test_create_profile_with_settings_atomically(client):
    result = await create_profile(
        client,
        {
            "alias": "Perfil con horarios",
            "age": 9,
            "avatar": "frog",
            "settings": {
                "school_start": "08:00",
                "school_end": "12:30",
                "sleep_start": "22:00",
                "sleep_end": "07:00",
            },
        },
    )
    assert result["active"] is True
    assert result["settings"]["school_start"] == "08:00:00"
    assert result["settings"]["sleep_end"] == "07:00:00"
    stored = await client.get(f"/api/v1/profiles/{result['id']}")
    stored_settings = stored.json()["settings"]
    assert stored_settings["school_start"] == "08:00:00"
    assert stored_settings["school_end"] == "12:30:00"
    assert stored_settings["sleep_start"] == "22:00:00"
    assert stored_settings["sleep_end"] == "07:00:00"


@pytest.mark.asyncio
async def test_create_profile_rejects_settings_without_school_schedule(client):
    response = await client.post(
        "/api/v1/profiles",
        json={
            "alias": "Perfil inválido",
            "age": 9,
            "avatar": "cat",
            "settings": {"sleep_start": "22:00", "sleep_end": "07:00"},
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_empty_settings_does_not_require_a_record(client):
    profile = await create_profile(client)
    response = await client.get(f"/api/v1/profiles/{profile['id']}/settings")
    assert response.status_code == 200
    assert response.json() == {
        "school_start": None,
        "school_end": None,
        "sleep_start": None,
        "sleep_end": None,
        "profile_id": profile["id"],
        "created_at": None,
        "updated_at": None,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"school_start": "08:00", "school_end": "12:30"},
        {"school_start": "08:00", "school_end": "12:30", "sleep_start": "22:00", "sleep_end": "07:00"},
    ],
)
async def test_create_settings_with_valid_schedules(client, payload):
    profile = await create_profile(client)
    response = await client.put(f"/api/v1/profiles/{profile['id']}/settings", json=payload)
    assert response.status_code == 200
    for key, value in payload.items():
        assert response.json()[key] == f"{value}:00"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"school_start": "08:00"},
        {"school_end": "12:30"},
        {"school_start": "08:00", "school_end": "12:30", "sleep_end": "07:00"},
    ],
)
async def test_reject_incomplete_schedule_pairs(client, payload):
    profile = await create_profile(client)
    response = await client.put(f"/api/v1/profiles/{profile['id']}/settings", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_reject_equal_school_times(client):
    profile = await create_profile(client)
    response = await client.put(
        f"/api/v1/profiles/{profile['id']}/settings",
        json={"school_start": "08:00", "school_end": "08:00"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_reject_equal_sleep_times(client):
    profile = await create_profile(client)
    response = await client.put(
        f"/api/v1/profiles/{profile['id']}/settings",
        json={"school_start": "08:00", "school_end": "12:30", "sleep_start": "22:00", "sleep_end": "22:00"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_allow_sleep_schedule_across_midnight(client):
    profile = await create_profile(client)
    response = await client.put(
        f"/api/v1/profiles/{profile['id']}/settings",
        json={"school_start": "08:00", "school_end": "12:30", "sleep_start": "22:00", "sleep_end": "07:00"},
    )
    assert response.status_code == 200
    assert response.json()["sleep_start"] == "22:00:00"
    assert response.json()["sleep_end"] == "07:00:00"


@pytest.mark.asyncio
async def test_update_settings_keeps_single_resource(client):
    profile = await create_profile(client)
    url = f"/api/v1/profiles/{profile['id']}/settings"
    first = await client.put(url, json={"school_start": "08:00", "school_end": "12:30"})
    second = await client.put(url, json={"school_start": "09:00", "school_end": "13:00", "sleep_start": "22:00", "sleep_end": "07:00"})
    assert first.status_code == second.status_code == 200
    result = (await client.get(url)).json()
    assert result["school_start"] == "09:00:00"
    assert result["sleep_start"] == "22:00:00"
    assert result["created_at"] == first.json()["created_at"]


@pytest.mark.asyncio
async def test_settings_for_missing_profile_returns_404(client):
    await create_profile(client)
    missing_id = "00000000-0000-0000-0000-000000000000"
    assert (await client.get(f"/api/v1/profiles/{missing_id}/settings")).status_code == 403
    assert (await client.put(f"/api/v1/profiles/{missing_id}/settings", json={})).status_code == 403


@pytest.mark.asyncio
async def test_get_empty_activity_preferences_does_not_create_a_record(client):
    profile = await create_profile(client)
    response = await client.get(f"/api/v1/profiles/{profile['id']}/activity-preferences")
    assert response.status_code == 200
    assert response.json() == {
        "id": None,
        "profile_id": profile["id"],
        "interests": [],
        "considerations_answered": False,
        "considerations": [],
        "activities_to_avoid": [],
        "additional_notes": None,
        "created_at": None,
        "updated_at": None,
    }


@pytest.mark.asyncio
async def test_create_activity_preferences_with_interests_only(client):
    profile = await create_profile(client)
    response = await client.put(
        f"/api/v1/profiles/{profile['id']}/activity-preferences",
        json={"interests": ["DRAWING_CREATING", "READING_STORIES", "MUSIC"]},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["id"] is not None
    assert result["interests"] == ["DRAWING_CREATING", "READING_STORIES", "MUSIC"]
    assert result["considerations_answered"] is False
    assert result["considerations"] == []
    assert result["activities_to_avoid"] == []


@pytest.mark.asyncio
async def test_activity_preferences_records_explicit_no_considerations(client):
    profile = await create_profile(client)

    response = await client.put(
        f"/api/v1/profiles/{profile['id']}/activity-preferences",
        json={"considerations_answered": True, "considerations": []},
    )

    assert response.status_code == 200
    assert response.json()["considerations_answered"] is True
    assert response.json()["considerations"] == []


@pytest.mark.asyncio
async def test_activity_preferences_accepts_permanent_consideration(client):
    profile = await create_profile(client)
    response = await client.put(
        f"/api/v1/profiles/{profile['id']}/activity-preferences",
        json={
            "considerations_answered": True,
            "considerations": [
                {
                    "type": "VISUAL",
                    "duration_type": "PERMANENT",
                    "valid_until": None,
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["considerations"] == [
        {
            "type": "VISUAL",
            "duration_type": "PERMANENT",
            "valid_until": None,
        }
    ]


@pytest.mark.asyncio
async def test_activity_preferences_accepts_temporary_consideration(client):
    profile = await create_profile(client)
    valid_until = (date.today() + timedelta(days=10)).isoformat()

    response = await client.put(
        f"/api/v1/profiles/{profile['id']}/activity-preferences",
        json={
            "considerations_answered": True,
            "considerations": [
                {
                    "type": "LOWER_BODY_MOBILITY",
                    "duration_type": "TEMPORARY",
                    "valid_until": valid_until,
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["considerations"][0]["valid_until"] == valid_until


@pytest.mark.asyncio
async def test_activity_preferences_accepts_mixed_consideration_durations(client):
    profile = await create_profile(client)
    valid_until = (date.today() + timedelta(days=10)).isoformat()
    considerations = [
        {
            "type": "VISUAL",
            "duration_type": "PERMANENT",
            "valid_until": None,
        },
        {
            "type": "SOUND_SENSITIVITY",
            "duration_type": "TEMPORARY",
            "valid_until": valid_until,
        },
    ]

    response = await client.put(
        f"/api/v1/profiles/{profile['id']}/activity-preferences",
        json={"considerations_answered": True, "considerations": considerations},
    )

    assert response.status_code == 200
    assert response.json()["considerations"] == considerations


@pytest.mark.asyncio
async def test_activity_preferences_accepts_both_mobility_considerations(client):
    profile = await create_profile(client)
    considerations = [
        {
            "type": "UPPER_BODY_MOBILITY",
            "duration_type": "PERMANENT",
            "valid_until": None,
        },
        {
            "type": "LOWER_BODY_MOBILITY",
            "duration_type": "PERMANENT",
            "valid_until": None,
        },
    ]

    response = await client.put(
        f"/api/v1/profiles/{profile['id']}/activity-preferences",
        json={"considerations_answered": True, "considerations": considerations},
    )

    assert response.status_code == 200
    assert response.json()["considerations"] == considerations


@pytest.mark.asyncio
async def test_activity_preferences_rejects_duplicate_consideration_type(client):
    profile = await create_profile(client)
    consideration = {
        "type": "AUDITORY",
        "duration_type": "PERMANENT",
        "valid_until": None,
    }

    response = await client.put(
        f"/api/v1/profiles/{profile['id']}/activity-preferences",
        json={
            "considerations_answered": True,
            "considerations": [consideration, consideration],
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_activity_preferences_rejects_temporary_without_valid_until(client):
    profile = await create_profile(client)
    response = await client.put(
        f"/api/v1/profiles/{profile['id']}/activity-preferences",
        json={
            "considerations_answered": True,
            "considerations": [
                {
                    "type": "VISUAL",
                    "duration_type": "TEMPORARY",
                    "valid_until": None,
                }
            ],
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_activity_preferences_rejects_permanent_with_valid_until(client):
    profile = await create_profile(client)
    response = await client.put(
        f"/api/v1/profiles/{profile['id']}/activity-preferences",
        json={
            "considerations_answered": True,
            "considerations": [
                {
                    "type": "VISUAL",
                    "duration_type": "PERMANENT",
                    "valid_until": (date.today() + timedelta(days=10)).isoformat(),
                }
            ],
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_activity_preferences_rejects_expired_temporary_consideration(client):
    profile = await create_profile(client)
    response = await client.put(
        f"/api/v1/profiles/{profile['id']}/activity-preferences",
        json={
            "considerations_answered": True,
            "considerations": [
                {
                    "type": "VISUAL",
                    "duration_type": "TEMPORARY",
                    "valid_until": (date.today() - timedelta(days=1)).isoformat(),
                }
            ],
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_activity_preferences_update_replaces_consideration_set(client):
    profile = await create_profile(client)
    url = f"/api/v1/profiles/{profile['id']}/activity-preferences"
    first = await client.put(
        url,
        json={
            "considerations_answered": True,
            "considerations": [
                {
                    "type": "VISUAL",
                    "duration_type": "PERMANENT",
                    "valid_until": None,
                }
            ],
        },
    )
    replacement = [
        {
            "type": "AUDITORY",
            "duration_type": "TEMPORARY",
            "valid_until": (date.today() + timedelta(days=10)).isoformat(),
        }
    ]

    second = await client.put(
        url,
        json={"considerations_answered": True, "considerations": replacement},
    )

    assert first.status_code == second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["considerations"] == replacement
    assert (await client.get(url)).json()["considerations"] == replacement


@pytest.mark.asyncio
async def test_activity_preferences_partial_update_preserves_considerations(client):
    profile = await create_profile(client)
    url = f"/api/v1/profiles/{profile['id']}/activity-preferences"
    first = await client.put(
        url,
        json={
            "interests": ["MUSIC"],
            "considerations_answered": True,
            "considerations": [
                {
                    "type": "AUDITORY",
                    "duration_type": "PERMANENT",
                    "valid_until": None,
                }
            ],
            "additional_notes": "Nota inicial",
        },
    )

    second = await client.put(url, json={"activities_to_avoid": ["LOUD_SOUNDS"]})

    assert first.status_code == second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["created_at"] == first.json()["created_at"]
    assert second.json()["interests"] == ["MUSIC"]
    assert second.json()["considerations_answered"] is True
    assert second.json()["considerations"] == first.json()["considerations"]
    assert second.json()["additional_notes"] == "Nota inicial"
    assert second.json()["activities_to_avoid"] == ["LOUD_SOUNDS"]


@pytest.mark.asyncio
async def test_marking_considerations_unanswered_clears_existing_set(client):
    profile = await create_profile(client)
    url = f"/api/v1/profiles/{profile['id']}/activity-preferences"
    await client.put(
        url,
        json={
            "considerations_answered": True,
            "considerations": [
                {
                    "type": "VISUAL",
                    "duration_type": "PERMANENT",
                    "valid_until": None,
                }
            ],
        },
    )

    response = await client.put(url, json={"considerations_answered": False})

    assert response.status_code == 200
    assert response.json()["considerations_answered"] is False
    assert response.json()["considerations"] == []


@pytest.mark.asyncio
async def test_unanswered_considerations_rejects_nonempty_list(client):
    profile = await create_profile(client)
    response = await client.put(
        f"/api/v1/profiles/{profile['id']}/activity-preferences",
        json={
            "considerations_answered": False,
            "considerations": [
                {
                    "type": "VISUAL",
                    "duration_type": "PERMANENT",
                    "valid_until": None,
                }
            ],
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_activity_preferences_get_returns_exact_persisted_state(client):
    profile = await create_profile(client)
    url = f"/api/v1/profiles/{profile['id']}/activity-preferences"
    payload = {
        "interests": ["GAMES_CHALLENGES", "BUILDING"],
        "considerations_answered": True,
        "considerations": [
            {
                "type": "VISUAL",
                "duration_type": "PERMANENT",
                "valid_until": None,
            },
            {
                "type": "LOWER_BODY_MOBILITY",
                "duration_type": "TEMPORARY",
                "valid_until": (date.today() + timedelta(days=10)).isoformat(),
            },
        ],
        "activities_to_avoid": ["RUNNING", "JUMPING"],
        "additional_notes": "Mantener como texto informativo.",
    }

    updated = await client.put(url, json=payload)
    read = await client.get(url)

    assert updated.status_code == read.status_code == 200
    assert read.json() == updated.json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"interests": ["INVALID"]},
        {
            "considerations_answered": True,
            "considerations": [
                {
                    "type": "INVALID",
                    "duration_type": "PERMANENT",
                    "valid_until": None,
                }
            ],
        },
        {"activities_to_avoid": ["INVALID"]},
    ],
)
async def test_reject_invalid_activity_preference_enums(client, payload):
    profile = await create_profile(client)
    response = await client.put(
        f"/api/v1/profiles/{profile['id']}/activity-preferences",
        json=payload,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"interests": ["MUSIC", "MUSIC"]},
        {"activities_to_avoid": ["RUNNING", "RUNNING"]},
    ],
)
async def test_reject_duplicate_activity_preference_values(client, payload):
    profile = await create_profile(client)
    response = await client.put(
        f"/api/v1/profiles/{profile['id']}/activity-preferences",
        json=payload,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_activity_preferences_additional_notes_limit(client):
    profile = await create_profile(client)
    response = await client.put(
        f"/api/v1/profiles/{profile['id']}/activity-preferences",
        json={"additional_notes": "x" * 501},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_activity_preferences_for_missing_profile_returns_404(client):
    await create_profile(client)
    missing_id = "00000000-0000-0000-0000-000000000000"
    url = f"/api/v1/profiles/{missing_id}/activity-preferences"
    assert (await client.get(url)).status_code == 403
    assert (await client.put(url, json={})).status_code == 403


@pytest.mark.asyncio
async def test_activity_preferences_are_isolated_by_profile(client):
    first_profile = await create_profile(client)
    second_profile = await create_profile(client)
    first_url = f"/api/v1/profiles/{first_profile['id']}/activity-preferences"
    second_url = f"/api/v1/profiles/{second_profile['id']}/activity-preferences"

    first = await client.put(first_url, json={"interests": ["WRITING"]})
    second = await client.put(second_url, json={"interests": ["IMAGINATIVE_PLAY"]})
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] != second.json()["id"]
    assert (await client.get(first_url)).json()["interests"] == ["WRITING"]
    assert (await client.get(second_url)).json()["interests"] == ["IMAGINATIVE_PLAY"]


@pytest.mark.asyncio
@pytest.mark.parametrize("context_type", ["VACATION", "ILLNESS"])
async def test_create_profile_context(client, context_type):
    profile = await create_profile(client)
    context = await create_context(client, profile["id"], type=context_type)
    assert context["profile_id"] == profile["id"]
    assert context["type"] == context_type
    assert context["start_date"] == "2026-09-01"
    assert context["end_date"] == "2026-09-07"


@pytest.mark.asyncio
async def test_create_open_routine_change_context(client):
    profile = await create_profile(client)
    context = await create_context(
        client,
        profile["id"],
        type="ROUTINE_CHANGE",
        start_date="2026-09-28",
        end_date=None,
        description="Cambio de horarios familiares",
    )
    assert context["type"] == "ROUTINE_CHANGE"
    assert context["end_date"] is None


@pytest.mark.asyncio
async def test_list_profile_contexts_orders_by_start_date_desc(client):
    profile = await create_profile(client)
    await create_context(client, profile["id"], start_date="2026-08-01", end_date="2026-08-07")
    await create_context(client, profile["id"], type="ILLNESS", start_date="2026-10-01", end_date="2026-10-02")
    await create_context(client, profile["id"], type="OTHER", start_date="2026-09-15", end_date=None)

    response = await client.get(f"/api/v1/profiles/{profile['id']}/contexts")
    assert response.status_code == 200
    assert [item["start_date"] for item in response.json()] == ["2026-10-01", "2026-09-15", "2026-08-01"]


@pytest.mark.asyncio
async def test_list_active_profile_contexts(client):
    profile = await create_profile(client)
    today = datetime.now(timezone.utc).date()
    active_bounded = await create_context(
        client,
        profile["id"],
        start_date=(today - timedelta(days=1)).isoformat(),
        end_date=(today + timedelta(days=1)).isoformat(),
    )
    active_open = await create_context(
        client,
        profile["id"],
        type="ROUTINE_CHANGE",
        start_date=(today - timedelta(days=2)).isoformat(),
        end_date=None,
    )
    await create_context(
        client,
        profile["id"],
        type="SCHOOL_ACTIVITY",
        start_date=(today + timedelta(days=1)).isoformat(),
        end_date=None,
    )
    await create_context(
        client,
        profile["id"],
        type="ILLNESS",
        start_date=(today - timedelta(days=3)).isoformat(),
        end_date=(today - timedelta(days=1)).isoformat(),
    )

    response = await client.get(f"/api/v1/profiles/{profile['id']}/contexts/active")
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [active_bounded["id"], active_open["id"]]


@pytest.mark.asyncio
async def test_reject_profile_context_with_end_before_start(client):
    profile = await create_profile(client)
    response = await client.post(
        f"/api/v1/profiles/{profile['id']}/contexts",
        json={"type": "VACATION", "start_date": "2026-10-10", "end_date": "2026-10-09"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_update_profile_context_partially(client):
    profile = await create_profile(client)
    context = await create_context(client, profile["id"])
    response = await client.put(
        f"/api/v1/profiles/{profile['id']}/contexts/{context['id']}",
        json={"type": "OTHER", "end_date": None, "description": "Viaje familiar"},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["type"] == "OTHER"
    assert result["start_date"] == context["start_date"]
    assert result["end_date"] is None
    assert result["description"] == "Viaje familiar"


@pytest.mark.asyncio
async def test_reject_invalid_date_range_on_profile_context_update(client):
    profile = await create_profile(client)
    context = await create_context(client, profile["id"])
    response = await client.put(
        f"/api/v1/profiles/{profile['id']}/contexts/{context['id']}",
        json={"end_date": "2026-08-31"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_delete_profile_context(client):
    profile = await create_profile(client)
    context = await create_context(client, profile["id"])
    url = f"/api/v1/profiles/{profile['id']}/contexts/{context['id']}"
    response = await client.delete(url)
    assert response.status_code == 204
    assert (await client.get(f"/api/v1/profiles/{profile['id']}/contexts")).json() == []


@pytest.mark.asyncio
async def test_profile_context_for_missing_profile_returns_404(client):
    await create_profile(client)
    missing_id = "00000000-0000-0000-0000-000000000000"
    response = await client.post(
        f"/api/v1/profiles/{missing_id}/contexts",
        json={"type": "VACATION", "start_date": "2026-10-01"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_missing_profile_context_returns_404(client):
    profile = await create_profile(client)
    missing_id = "00000000-0000-0000-0000-000000000000"
    url = f"/api/v1/profiles/{profile['id']}/contexts/{missing_id}"
    assert (await client.put(url, json={"description": "No existe"})).status_code == 404
    assert (await client.delete(url)).status_code == 404


@pytest.mark.asyncio
async def test_cannot_update_or_delete_context_from_another_profile(client):
    owner = await create_profile(client)
    other = await create_profile(client)
    context = await create_context(client, owner["id"])
    foreign_url = f"/api/v1/profiles/{other['id']}/contexts/{context['id']}"

    assert (await client.put(foreign_url, json={"description": "Cambio indebido"})).status_code == 404
    assert (await client.delete(foreign_url)).status_code == 404
    owner_contexts = (await client.get(f"/api/v1/profiles/{owner['id']}/contexts")).json()
    assert owner_contexts == [context]


@pytest.mark.asyncio
async def test_register_usage_and_get_history(client, adult_session, monitoring_authorization):
    profile = await create_profile(client)
    await monitoring_authorization(profile["id"])
    response = await client.post(
        f"/api/v1/profiles/{profile['id']}/usage",
        headers=profile["_device_headers"],
        json=usage_payload(),
    )
    assert response.status_code == 201
    assert response.json()["usage"]["applications"][0]["usage_minutes"] == 80
    assert response.json()["usage"]["total_usage_minutes"] == 230
    assert response.json()["usage"]["calculation_version"] == "1.0"
    assert response.json()["usage"]["coverage_status"] == "UNKNOWN"
    headers, _ = await adult_session(profile["id"])
    history = await client.get(f"/api/v1/profiles/{profile['id']}/usage", headers=headers)
    assert history.status_code == 200
    assert len(history.json()) == 1


@pytest.mark.asyncio
async def test_exact_usage_retry_is_idempotent(client, monitoring_authorization):
    profile = await create_profile(client)
    await monitoring_authorization(profile["id"])
    url = f"/api/v1/profiles/{profile['id']}/usage"
    assert (
        await client.post(url, headers=profile["_device_headers"], json=usage_payload())
    ).status_code == 201
    retry = await client.post(url, headers=profile["_device_headers"], json=usage_payload())
    assert retry.status_code == 200
    assert retry.json()["result"] == "unchanged"


@pytest.mark.asyncio
async def test_analysis_uses_mock(client, adult_session, monitoring_authorization):
    profile = await create_profile(client)
    await monitoring_authorization(profile["id"])
    await client.post(
        f"/api/v1/profiles/{profile['id']}/usage",
        headers=profile["_device_headers"],
        json=usage_payload(),
    )
    headers, _ = await adult_session(profile["id"])
    response = await client.post(
        f"/api/v1/profiles/{profile['id']}/analysis",
        json={"date": "2026-08-15"},
        headers=headers,
    )
    assert response.status_code == 201
    result = response.json()
    assert result["anomaly_detected"] is False
    assert result["anomaly_score"] is None
    assert result["risk_level"] == "NORMAL"
    assert result["model_version"] == "mock-v1"


@pytest.mark.asyncio
async def test_usage_newer_snapshot_replaces_and_historical_retry_is_idempotent(
    client,
    monitoring_authorization,
):
    profile = await create_profile(client)
    await monitoring_authorization(profile["id"])
    url = f"/api/v1/profiles/{profile['id']}/usage"
    first = usage_payload()
    newer = usage_payload(
        snapshot_id="00000000-0000-0000-0000-000000000816",
        captured_at="2026-08-15T21:00:00Z",
        period_end_at="2026-08-15T20:00:00Z",
        total_usage_minutes=300,
        applications=[
            {
                "package_name": "com.example.app",
                "app_name": "Ejemplo",
                "category": None,
                "usage_minutes": 100,
            }
        ],
    )

    assert (await client.post(url, headers=profile["_device_headers"], json=first)).status_code == 201
    updated = await client.post(url, headers=profile["_device_headers"], json=newer)
    retry = await client.post(url, headers=profile["_device_headers"], json=first)

    assert updated.status_code == 200
    assert updated.json()["result"] == "updated"
    assert updated.json()["usage"]["total_usage_minutes"] == 300
    assert retry.status_code == 200
    assert retry.json()["result"] == "unchanged"
    assert retry.json()["usage"]["total_usage_minutes"] == 300

    stale = usage_payload(
        snapshot_id="00000000-0000-0000-0000-000000000817",
        captured_at="2026-08-15T20:30:00Z",
    )
    rejected = await client.post(url, headers=profile["_device_headers"], json=stale)
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "stale_snapshot"


@pytest.mark.asyncio
async def test_historical_usage_payload_and_legacy_hash_remain_idempotent(
    client,
    db_session,
    monitoring_authorization,
):
    profile = await create_profile(client)
    await monitoring_authorization(profile["id"])
    payload = usage_payload()
    url = f"/api/v1/profiles/{profile['id']}/usage"
    created = await client.post(url, headers=profile["_device_headers"], json=payload)
    assert created.status_code == 201
    assert created.json()["usage"]["last_night_activity_at"] is None
    assert created.json()["usage"]["metric_statuses"]["last_night_activity_at"] == "UNAVAILABLE"

    parsed = DailyUsageCreate.model_validate(payload)
    legacy_hash = UsageService._legacy_content_hash(parsed)
    snapshot = await db_session.scalar(
        select(UsageSnapshot).where(UsageSnapshot.id == parsed.snapshot_id)
    )
    assert snapshot is not None
    assert legacy_hash is not None
    snapshot.content_hash = legacy_hash
    await db_session.commit()

    retry = await client.post(url, headers=profile["_device_headers"], json=payload)
    assert retry.status_code == 200
    assert retry.json()["result"] == "unchanged"


@pytest.mark.asyncio
async def test_last_night_activity_uses_latest_compatible_device_timestamp(
    client,
    monitoring_authorization,
):
    profile = await create_profile(client)
    await monitoring_authorization(profile["id"])
    url = f"/api/v1/profiles/{profile['id']}/usage"
    first = usage_payload(
        last_night_activity_at="2026-08-15T06:10:00Z",
        metric_statuses={
            **usage_payload()["metric_statuses"],
            "last_night_activity_at": "MEASURED",
        },
    )
    assert (await client.post(url, headers=profile["_device_headers"], json=first)).status_code == 201

    link = await client.post(f"/api/v1/profiles/{profile['id']}/device-link-codes")
    claimed = await client.post(
        "/api/v1/device-links/claim",
        json={
            "code": link.json()["code"],
            "installation_id": f"second-{uuid.uuid4()}",
            "platform": "ANDROID",
        },
    )
    second_headers = {"Authorization": f"Bearer {claimed.json()['access_token']}"}
    second = usage_payload(
        snapshot_id="00000000-0000-0000-0000-000000000821",
        last_night_activity_at="2026-08-15T06:50:00Z",
        metric_statuses={
            **usage_payload()["metric_statuses"],
            "last_night_activity_at": "MEASURED",
        },
    )
    response = await client.post(url, headers=second_headers, json=second)
    usage = response.json()["usage"]

    assert response.status_code == 200
    assert usage["last_night_activity_at"] == "2026-08-15T06:50:00Z"
    assert usage["metric_statuses"]["last_night_activity_at"] == "MEASURED"
    assert sorted(source["last_night_activity_at"] for source in usage["sources"]) == [
        "2026-08-15T06:10:00Z",
        "2026-08-15T06:50:00Z",
    ]


@pytest.mark.asyncio
async def test_last_night_activity_is_incomplete_across_calculation_versions(
    client,
    monitoring_authorization,
):
    profile = await create_profile(client)
    await monitoring_authorization(profile["id"])
    url = f"/api/v1/profiles/{profile['id']}/usage"
    statuses = {
        **usage_payload()["metric_statuses"],
        "last_night_activity_at": "MEASURED",
    }
    first = usage_payload(
        calculation_version="android-collector-1.1",
        last_night_activity_at="2026-08-15T06:10:00Z",
        metric_statuses=statuses,
    )
    assert (await client.post(url, headers=profile["_device_headers"], json=first)).status_code == 201

    link = await client.post(f"/api/v1/profiles/{profile['id']}/device-link-codes")
    claimed = await client.post(
        "/api/v1/device-links/claim",
        json={
            "code": link.json()["code"],
            "installation_id": f"second-{uuid.uuid4()}",
            "platform": "ANDROID",
        },
    )
    second = usage_payload(
        snapshot_id="00000000-0000-0000-0000-000000000823",
        calculation_version="android-collector-2.0",
        last_night_activity_at="2026-08-15T06:50:00Z",
        metric_statuses=statuses,
    )
    response = await client.post(
        url,
        headers={"Authorization": f"Bearer {claimed.json()['access_token']}"},
        json=second,
    )

    assert response.status_code == 200
    assert response.json()["usage"]["last_night_activity_at"] is None
    assert response.json()["usage"]["metric_statuses"]["last_night_activity_at"] == "INCOMPLETE"


@pytest.mark.asyncio
async def test_last_night_activity_rejects_missing_or_out_of_window_evidence(
    client,
    monitoring_authorization,
):
    profile = await create_profile(client)
    await monitoring_authorization(profile["id"])
    base_statuses = usage_payload()["metric_statuses"]
    url = f"/api/v1/profiles/{profile['id']}/usage"

    missing = usage_payload(
        metric_statuses={**base_statuses, "last_night_activity_at": "MEASURED"},
    )
    outside = usage_payload(
        snapshot_id="00000000-0000-0000-0000-000000000822",
        last_night_activity_at="2026-08-15T12:00:00Z",
        metric_statuses={**base_statuses, "last_night_activity_at": "MEASURED"},
    )

    missing_response = await client.post(url, headers=profile["_device_headers"], json=missing)
    outside_response = await client.post(url, headers=profile["_device_headers"], json=outside)
    assert missing_response.status_code == 422
    assert outside_response.status_code == 422


@pytest.mark.asyncio
async def test_usage_snapshot_id_cannot_be_reused_with_different_content(
    client,
    monitoring_authorization,
):
    profile = await create_profile(client)
    await monitoring_authorization(profile["id"])
    url = f"/api/v1/profiles/{profile['id']}/usage"
    payload = usage_payload()
    assert (await client.post(url, headers=profile["_device_headers"], json=payload)).status_code == 201
    changed = {**payload, "total_usage_minutes": 231}
    conflict = await client.post(url, headers=profile["_device_headers"], json=changed)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "snapshot_conflict"


@pytest.mark.asyncio
async def test_new_null_metrics_replace_previous_values_without_merging(
    client,
    monitoring_authorization,
):
    profile = await create_profile(client)
    await monitoring_authorization(profile["id"])
    url = f"/api/v1/profiles/{profile['id']}/usage"
    assert (await client.post(url, headers=profile["_device_headers"], json=usage_payload())).status_code == 201
    unavailable = usage_payload(
        snapshot_id="00000000-0000-0000-0000-000000000818",
        captured_at="2026-08-15T21:00:00Z",
        period_end_at="2026-08-15T20:00:00Z",
        total_usage_minutes=None,
        session_count=None,
        night_usage_minutes=None,
        max_session_minutes=None,
        unlock_count=None,
        applications=None,
        metric_statuses={
            "total_usage_minutes": "CAPTURE_ERROR",
            "session_count": "CAPTURE_ERROR",
            "night_usage_minutes": "CAPTURE_ERROR",
            "max_session_minutes": "CAPTURE_ERROR",
            "unlock_count": "CAPTURE_ERROR",
            "applications": "CAPTURE_ERROR",
        },
        night_window=None,
    )
    response = await client.post(url, headers=profile["_device_headers"], json=unavailable)
    usage = response.json()["usage"]
    assert response.status_code == 200
    assert usage["total_usage_minutes"] is None
    assert usage["applications"] is None
    assert usage["metric_statuses"]["total_usage_minutes"] == "CAPTURE_ERROR"


@pytest.mark.asyncio
async def test_usage_multiple_devices_preserve_non_uniform_periods(
    client,
    monitoring_authorization,
):
    profile = await create_profile(client)
    await monitoring_authorization(profile["id"])
    url = f"/api/v1/profiles/{profile['id']}/usage"
    first = await client.post(url, headers=profile["_device_headers"], json=usage_payload())
    assert first.status_code == 201

    link = await client.post(f"/api/v1/profiles/{profile['id']}/device-link-codes")
    assert link.status_code == 201
    claimed = await client.post(
        "/api/v1/device-links/claim",
        json={
            "code": link.json()["code"],
            "installation_id": f"second-{uuid.uuid4()}",
            "platform": "ANDROID",
        },
    )
    assert claimed.status_code == 200
    second_headers = {"Authorization": f"Bearer {claimed.json()['access_token']}"}
    second_payload = usage_payload(
        snapshot_id="00000000-0000-0000-0000-000000000819",
        captured_at="2026-08-15T18:00:00Z",
        period_start_at="2026-08-15T08:00:00Z",
        period_end_at="2026-08-15T17:00:00Z",
        coverage_status="PARTIAL",
        total_usage_minutes=30,
        session_count=4,
        night_usage_minutes=0,
        max_session_minutes=10,
        unlock_count=5,
        applications=[
            {
                "package_name": "com.example.app",
                "app_name": "Ejemplo",
                "category": None,
                "usage_minutes": 20,
            }
        ],
    )
    response = await client.post(url, headers=second_headers, json=second_payload)
    usage = response.json()["usage"]

    assert response.status_code == 200
    assert usage["source_count"] == 2
    assert usage["periods_uniform"] is False
    assert usage["period_start_at"] is None
    assert usage["period_end_at"] is None
    assert len(usage["sources"]) == 2
    assert sorted(source["total_usage_minutes"] for source in usage["sources"]) == [30, 230]
    assert usage["coverage_status"] == "PARTIAL"
    assert usage["total_usage_minutes"] == 260
    assert usage["applications"][0]["usage_minutes"] == 100
    assert usage["last_night_activity_at"] is None
    assert usage["metric_statuses"]["last_night_activity_at"] == "INCOMPLETE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "expected_fragment"),
    [
        ({"coverage_status": "COMPLETE"}, "coverage_status"),
        ({"timezone": "Not/A_Zone"}, "timezone"),
        ({"captured_at": "2026-08-15T18:00:00Z"}, "captured_at"),
        (
            {
                "night_usage_minutes": None,
                "metric_statuses": {
                    "total_usage_minutes": "MEASURED",
                    "session_count": "MEASURED",
                    "night_usage_minutes": "MEASURED",
                    "max_session_minutes": "MEASURED",
                    "unlock_count": "MEASURED",
                    "applications": "MEASURED",
                },
            },
            "night_usage_minutes",
        ),
    ],
)
async def test_usage_contract_rejects_unsupported_coverage_and_inconsistent_metadata(
    client,
    monitoring_authorization,
    changes,
    expected_fragment,
):
    profile = await create_profile(client)
    await monitoring_authorization(profile["id"])
    response = await client.post(
        f"/api/v1/profiles/{profile['id']}/usage",
        headers=profile["_device_headers"],
        json=usage_payload(**changes),
    )
    assert response.status_code == 422
    assert expected_fragment in response.text


@pytest.mark.asyncio
async def test_usage_persists_iana_timezone_period_and_overnight_definition(
    client,
    monitoring_authorization,
):
    profile = await create_profile(client)
    await monitoring_authorization(profile["id"])
    payload = usage_payload(
        snapshot_id="00000000-0000-0000-0000-000000001016",
        date="2026-10-16",
        period_start_at="2026-10-16T00:00:00-03:00",
        period_end_at="2026-10-16T17:00:00-03:00",
        captured_at="2026-10-16T20:30:00Z",
        timezone="America/Argentina/Buenos_Aires",
        calculation_version="android-collector-2.1",
    )
    response = await client.post(
        f"/api/v1/profiles/{profile['id']}/usage",
        headers=profile["_device_headers"],
        json=payload,
    )
    usage = response.json()["usage"]
    assert response.status_code == 201
    assert usage["timezone"] == "America/Argentina/Buenos_Aires"
    assert usage["sources"][0]["calculation_version"] == "android-collector-2.1"
    assert usage["night_window"] == {
        "source": "PROFILE_SLEEP_SCHEDULE",
        "start_local_time": "22:00:00",
        "end_local_time": "07:00:00",
    }


@pytest.mark.asyncio
async def test_usage_history_supports_inclusive_date_filters(
    client,
    monitoring_authorization,
):
    profile = await create_profile(client)
    await monitoring_authorization(profile["id"])
    url = f"/api/v1/profiles/{profile['id']}/usage"
    assert (await client.post(url, headers=profile["_device_headers"], json=usage_payload())).status_code == 201
    second = usage_payload(
        snapshot_id="00000000-0000-0000-0000-000000000816",
        date="2026-08-16",
        period_start_at="2026-08-16T00:00:00Z",
        period_end_at="2026-08-16T19:00:00Z",
        captured_at="2026-08-16T20:00:00Z",
    )
    assert (await client.post(url, headers=profile["_device_headers"], json=second)).status_code == 201
    history = await client.get(f"{url}?date_from=2026-08-16&date_to=2026-08-16")
    assert history.status_code == 200
    assert [item["date"] for item in history.json()] == ["2026-08-16"]
    assert (await client.get(f"{url}?date_from=2026-08-17&date_to=2026-08-16")).status_code == 422


@pytest.mark.asyncio
async def test_concurrent_first_usage_snapshots_leave_newest_as_head(
    client,
    monitoring_authorization,
):
    profile = await create_profile(client)
    await monitoring_authorization(profile["id"])
    url = f"/api/v1/profiles/{profile['id']}/usage"
    older = usage_payload()
    newer = usage_payload(
        snapshot_id="00000000-0000-0000-0000-000000000820",
        captured_at="2026-08-15T21:00:00Z",
        period_end_at="2026-08-15T20:00:00Z",
        total_usage_minutes=310,
    )
    responses = await asyncio.gather(
        client.post(url, headers=profile["_device_headers"], json=older),
        client.post(url, headers=profile["_device_headers"], json=newer),
    )
    assert sorted(response.status_code for response in responses) == [200, 201]
    current = await client.get(f"{url}/2026-08-15")
    assert current.status_code == 200
    assert current.json()["total_usage_minutes"] == 310
