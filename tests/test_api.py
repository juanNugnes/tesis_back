import pytest


async def create_profile(client):
    response = await client.post("/api/v1/profiles", json={"alias": "Perfil A", "age": 9, "avatar": "fox"})
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
