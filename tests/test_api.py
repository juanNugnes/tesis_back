from datetime import datetime, timedelta, timezone

import pytest


async def create_profile(client):
    response = await client.post("/api/v1/profiles", json={"alias": "Perfil A", "age": 9, "avatar": "fox"})
    assert response.status_code == 201
    return response.json()


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


def usage_payload():
    return {
        "date": "2026-08-15",
        "total_usage_minutes": 230,
        "session_count": 25,
        "night_usage_minutes": 45,
        "max_session_minutes": 60,
        "unlock_count": 35,
        "applications": [{"package_name": "com.example.app", "app_name": "Ejemplo", "category": "SOCIAL", "usage_minutes": 80}],
    }


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
async def test_update_active(client):
    profile = await create_profile(client)
    response = await client.put(f"/api/v1/profiles/{profile['id']}", json={"active": False})
    assert response.status_code == 200
    assert response.json()["active"] is False


@pytest.mark.asyncio
async def test_update_multiple_profile_fields(client):
    profile = await create_profile(client)
    response = await client.put(
        f"/api/v1/profiles/{profile['id']}",
        json={"alias": "Perfil C", "age": 11, "avatar": "lion", "active": False},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["alias"] == "Perfil C"
    assert result["age"] == 11
    assert result["avatar"] == "lion"
    assert result["active"] is False


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
    response = await client.post(
        "/api/v1/profiles",
        json={
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
    assert response.status_code == 201
    result = response.json()
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
    missing_id = "00000000-0000-0000-0000-000000000000"
    assert (await client.get(f"/api/v1/profiles/{missing_id}/settings")).status_code == 404
    assert (await client.put(f"/api/v1/profiles/{missing_id}/settings", json={})).status_code == 404


@pytest.mark.asyncio
async def test_get_empty_activity_preferences_does_not_create_a_record(client):
    profile = await create_profile(client)
    response = await client.get(f"/api/v1/profiles/{profile['id']}/activity-preferences")
    assert response.status_code == 200
    assert response.json() == {
        "id": None,
        "profile_id": profile["id"],
        "interests": [],
        "adaptation_type": "NONE",
        "considerations": [],
        "activities_to_avoid": [],
        "additional_notes": None,
        "valid_until": None,
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
    assert result["adaptation_type"] == "NONE"
    assert result["considerations"] == []
    assert result["activities_to_avoid"] == []


@pytest.mark.asyncio
async def test_upsert_temporary_activity_preferences(client):
    profile = await create_profile(client)
    payload = {
        "interests": ["GAMES_CHALLENGES", "BUILDING"],
        "adaptation_type": "TEMPORARY",
        "considerations": ["LEGS", "TEMPORARY_PHYSICAL_DISCOMFORT"],
        "activities_to_avoid": ["RUNNING", "JUMPING"],
        "additional_notes": "Por unos dias conviene evitar actividades de impacto.",
        "valid_until": "2026-10-15",
    }
    response = await client.put(f"/api/v1/profiles/{profile['id']}/activity-preferences", json=payload)
    assert response.status_code == 200
    result = response.json()
    for key, value in payload.items():
        assert result[key] == value
    assert (await client.get(f"/api/v1/profiles/{profile['id']}/activity-preferences")).json() == result


@pytest.mark.asyncio
async def test_update_activity_preferences_is_partial_and_keeps_single_resource(client):
    profile = await create_profile(client)
    url = f"/api/v1/profiles/{profile['id']}/activity-preferences"
    first = await client.put(
        url,
        json={
            "interests": ["MUSIC"],
            "adaptation_type": "TEMPORARY",
            "considerations": ["HEARING"],
            "additional_notes": "Nota inicial",
        },
    )
    second = await client.put(url, json={"activities_to_avoid": ["LOUD_SOUNDS"]})
    assert first.status_code == second.status_code == 200
    result = second.json()
    assert result["id"] == first.json()["id"]
    assert result["created_at"] == first.json()["created_at"]
    assert result["interests"] == ["MUSIC"]
    assert result["considerations"] == ["HEARING"]
    assert result["additional_notes"] == "Nota inicial"
    assert result["activities_to_avoid"] == ["LOUD_SOUNDS"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"interests": ["INVALID"]},
        {"adaptation_type": "INVALID"},
        {"considerations": ["INVALID"]},
        {"activities_to_avoid": ["INVALID"]},
    ],
)
async def test_reject_invalid_activity_preference_enums(client, payload):
    profile = await create_profile(client)
    response = await client.put(f"/api/v1/profiles/{profile['id']}/activity-preferences", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"interests": ["MUSIC", "MUSIC"]},
        {"considerations": ["VISION", "VISION"]},
        {"activities_to_avoid": ["RUNNING", "RUNNING"]},
    ],
)
async def test_reject_duplicate_activity_preference_values(client, payload):
    profile = await create_profile(client)
    response = await client.put(f"/api/v1/profiles/{profile['id']}/activity-preferences", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("adaptation_type", "valid_until"),
    [
        ("TEMPORARY", None),
        ("PERMANENT", None),
        ("NONE", None),
    ],
)
async def test_supported_adaptation_types_without_required_expiration(client, adaptation_type, valid_until):
    profile = await create_profile(client)
    response = await client.put(
        f"/api/v1/profiles/{profile['id']}/activity-preferences",
        json={
            "adaptation_type": adaptation_type,
            "interests": [],
            "considerations": [],
            "activities_to_avoid": [],
            "valid_until": valid_until,
        },
    )
    assert response.status_code == 200
    assert response.json()["adaptation_type"] == adaptation_type
    assert response.json()["valid_until"] is None


@pytest.mark.asyncio
async def test_none_adaptation_clears_valid_until_without_clearing_collections(client):
    profile = await create_profile(client)
    url = f"/api/v1/profiles/{profile['id']}/activity-preferences"
    await client.put(
        url,
        json={
            "interests": ["OUTDOORS"],
            "adaptation_type": "TEMPORARY",
            "considerations": ["MOBILITY"],
            "activities_to_avoid": ["PROLONGED_STANDING"],
            "valid_until": "2026-10-15",
        },
    )
    response = await client.put(url, json={"adaptation_type": "NONE"})
    assert response.status_code == 200
    result = response.json()
    assert result["valid_until"] is None
    assert result["interests"] == ["OUTDOORS"]
    assert result["considerations"] == ["MOBILITY"]
    assert result["activities_to_avoid"] == ["PROLONGED_STANDING"]


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
    missing_id = "00000000-0000-0000-0000-000000000000"
    url = f"/api/v1/profiles/{missing_id}/activity-preferences"
    assert (await client.get(url)).status_code == 404
    assert (await client.put(url, json={})).status_code == 404


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
    missing_id = "00000000-0000-0000-0000-000000000000"
    response = await client.post(
        f"/api/v1/profiles/{missing_id}/contexts",
        json={"type": "VACATION", "start_date": "2026-10-01"},
    )
    assert response.status_code == 404


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
async def test_register_usage_and_get_history(client):
    profile = await create_profile(client)
    response = await client.post(f"/api/v1/profiles/{profile['id']}/usage", json=usage_payload())
    assert response.status_code == 201
    assert response.json()["applications"][0]["usage_minutes"] == 80
    history = await client.get(f"/api/v1/profiles/{profile['id']}/usage")
    assert history.status_code == 200
    assert len(history.json()) == 1


@pytest.mark.asyncio
async def test_reject_duplicate_usage(client):
    profile = await create_profile(client)
    url = f"/api/v1/profiles/{profile['id']}/usage"
    assert (await client.post(url, json=usage_payload())).status_code == 201
    assert (await client.post(url, json=usage_payload())).status_code == 409


@pytest.mark.asyncio
async def test_analysis_uses_mock(client):
    profile = await create_profile(client)
    await client.post(f"/api/v1/profiles/{profile['id']}/usage", json=usage_payload())
    response = await client.post(f"/api/v1/profiles/{profile['id']}/analysis", json={"date": "2026-08-15"})
    assert response.status_code == 201
    result = response.json()
    assert result["anomaly_detected"] is False
    assert result["anomaly_score"] is None
    assert result["risk_level"] == "NORMAL"
    assert result["model_version"] == "mock-v1"
