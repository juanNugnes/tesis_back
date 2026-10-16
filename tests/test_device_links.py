import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.db.models import Device, Profile, ProfileLinkCode
from app.repositories.profile_repository import ProfileRepository
from app.services.profile_link_service import ProfileLinkService


async def create_profile(client, *, alias="Perfil Device", active=True):
    response = await client.post(
        "/api/v1/profiles",
        json={"alias": alias, "age": 9, "avatar": "fox", "active": active},
    )
    assert response.status_code == 201
    return response.json()


async def create_link_code(client, adult_session, profile_id):
    headers, _ = await adult_session(profile_id)
    response = await client.post(
        f"/api/v1/profiles/{profile_id}/device-link-codes",
        headers=headers,
    )
    assert response.status_code == 201
    return response.json()


async def device_count(db_session):
    return await db_session.scalar(select(func.count()).select_from(Device))


@pytest.mark.asyncio
async def test_register_device_consumes_bootstrap(client, db_session):
    profile = await create_profile(client)
    url = f"/api/v1/profiles/{profile['id']}/devices"
    payload = {"installation_id": "installation-one", "platform": "ANDROID"}

    headers = {"Authorization": f"Bearer {profile['bootstrap_token']}"}
    first = await client.post(url, headers=headers, json=payload)
    second = await client.post(url, headers=headers, json=payload)

    assert first.status_code == 201
    assert second.status_code == 401
    assert first.json()["profile_id"] == profile["id"]
    assert first.json()["active"] is True
    assert first.json()["last_seen_at"] is not None
    assert first.json()["token_type"] == "bearer"
    assert first.json()["access_token"]
    assert await device_count(db_session) == 1


@pytest.mark.asyncio
async def test_installation_cannot_move_to_another_profile(client):
    first_profile = await create_profile(client, alias="Primero")
    second_profile = await create_profile(client, alias="Segundo")
    payload = {"installation_id": "shared-installation", "platform": "ANDROID"}
    assert (
        await client.post(
            f"/api/v1/profiles/{first_profile['id']}/devices",
            headers={"Authorization": f"Bearer {first_profile['bootstrap_token']}"},
            json=payload,
        )
    ).status_code == 201

    response = await client.post(
        f"/api/v1/profiles/{second_profile['id']}/devices",
        headers={"Authorization": f"Bearer {second_profile['bootstrap_token']}"},
        json=payload,
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Installation is already linked to another profile"


@pytest.mark.asyncio
async def test_device_registration_validates_profile_and_platform(client):
    token_profile = await create_profile(client, alias="Token source")
    missing_id = uuid.uuid4()
    missing = await client.post(
        f"/api/v1/profiles/{missing_id}/devices",
        headers={"Authorization": f"Bearer {token_profile['bootstrap_token']}"},
        json={"installation_id": "missing-profile", "platform": "ANDROID"},
    )
    assert missing.status_code == 403

    inactive = await create_profile(client, active=False)
    rejected = await client.post(
        f"/api/v1/profiles/{inactive['id']}/devices",
        headers={"Authorization": f"Bearer {inactive['bootstrap_token']}"},
        json={"installation_id": "inactive-profile", "platform": "ANDROID"},
    )
    assert rejected.status_code == 409

    active = await create_profile(client, alias="Platform")
    invalid_platform = await client.post(
        f"/api/v1/profiles/{active['id']}/devices",
        headers={"Authorization": f"Bearer {active['bootstrap_token']}"},
        json={"installation_id": "ios-installation", "platform": "IOS"},
    )
    assert invalid_platform.status_code == 422


@pytest.mark.asyncio
async def test_generate_link_code_hashes_it_and_sets_expiration(client, db_session, adult_session):
    profile = await create_profile(client)
    before = datetime.now(timezone.utc)

    headers, _ = await adult_session(profile["id"])
    response = await client.post(
        f"/api/v1/profiles/{profile['id']}/device-link-codes",
        headers=headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert re.fullmatch(r"\d{6}", body["code"])
    assert body["purpose"] == "DEVICE_LINK"
    expires_at = datetime.fromisoformat(body["expires_at"])
    assert before + timedelta(minutes=9, seconds=55) < expires_at
    assert expires_at < before + timedelta(minutes=10, seconds=5)

    stored = await db_session.scalar(
        select(ProfileLinkCode).where(ProfileLinkCode.profile_id == uuid.UUID(profile["id"]))
    )
    assert stored is not None
    assert stored.code_hash != body["code"]
    assert len(stored.code_hash) == 64
    assert stored.purpose == "DEVICE_LINK"


@pytest.mark.asyncio
async def test_two_active_link_codes_can_coexist(client, db_session, adult_session):
    profile = await create_profile(client)
    first = await create_link_code(client, adult_session, profile["id"])
    second = await create_link_code(client, adult_session, profile["id"])

    assert first["code"] != second["code"]
    count = await db_session.scalar(
        select(func.count())
        .select_from(ProfileLinkCode)
        .where(ProfileLinkCode.profile_id == uuid.UUID(profile["id"]))
    )
    assert count == 2


@pytest.mark.asyncio
async def test_link_code_generation_retries_a_collision(client, db_session):
    profile_data = await create_profile(client)
    profile = await ProfileRepository(db_session).get(uuid.UUID(profile_data["id"]))
    assert profile is not None
    generated_values = iter(["123456", "123456", "654321"])
    service = ProfileLinkService(
        db_session,
        "test-hash-secret",
        code_generator=lambda: next(generated_values),
    )

    first = await service.generate(profile)
    second = await service.generate(profile)

    assert first.code == "123456"
    assert second.code == "654321"


@pytest.mark.asyncio
async def test_link_code_generation_validates_profile_state(client, adult_session):
    own_profile = await create_profile(client, alias="Own")
    headers, _ = await adult_session(own_profile["id"])
    missing = await client.post(
        f"/api/v1/profiles/{uuid.uuid4()}/device-link-codes",
        headers=headers,
    )
    assert missing.status_code == 403

    inactive = await create_profile(client, active=False)
    inactive_headers, _ = await adult_session(inactive["id"])
    response = await client.post(
        f"/api/v1/profiles/{inactive['id']}/device-link-codes",
        headers=inactive_headers,
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_claim_links_device_returns_profile_and_consumes_code(client, db_session, adult_session):
    profile = await create_profile(client)
    link_code = await create_link_code(client, adult_session, profile["id"])
    payload = {
        "code": link_code["code"],
        "installation_id": "claimed-installation",
        "platform": "ANDROID",
    }

    response = await client.post("/api/v1/device-links/claim", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["device"]["profile_id"] == profile["id"]
    assert body["device"]["installation_id"] == "claimed-installation"
    assert body["profile"]["id"] == profile["id"]
    assert body["profile"]["settings"]["profile_id"] == profile["id"]
    assert body["access_token"]
    assert body["token_type"] == "bearer"

    stored = await db_session.scalar(
        select(ProfileLinkCode).where(ProfileLinkCode.profile_id == uuid.UUID(profile["id"]))
    )
    assert stored is not None
    assert stored.used_at is not None

    second = await client.post("/api/v1/device-links/claim", json=payload)
    assert second.status_code == 410
    assert second.json()["detail"] == "Invalid or expired link code"


@pytest.mark.asyncio
async def test_expired_and_unknown_codes_do_not_create_devices(client, db_session, adult_session):
    profile = await create_profile(client)
    link_code = await create_link_code(client, adult_session, profile["id"])
    stored = await db_session.scalar(
        select(ProfileLinkCode).where(ProfileLinkCode.profile_id == uuid.UUID(profile["id"]))
    )
    assert stored is not None
    stored.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.commit()

    expired = await client.post(
        "/api/v1/device-links/claim",
        json={
            "code": link_code["code"],
            "installation_id": "expired-code-installation",
            "platform": "ANDROID",
        },
    )
    unknown = await client.post(
        "/api/v1/device-links/claim",
        json={
            "code": "999999",
            "installation_id": "unknown-code-installation",
            "platform": "ANDROID",
        },
    )

    assert expired.status_code == 410
    assert unknown.status_code == 410
    assert await device_count(db_session) == 0
    await db_session.refresh(stored)
    assert stored.used_at is None


@pytest.mark.asyncio
async def test_claim_is_idempotent_for_existing_profile_device(client, db_session, adult_session):
    profile = await create_profile(client)
    device_payload = {"installation_id": "already-linked", "platform": "ANDROID"}
    original = await client.post(
        f"/api/v1/profiles/{profile['id']}/devices",
        headers={"Authorization": f"Bearer {profile['bootstrap_token']}"},
        json=device_payload,
    )
    link_code = await create_link_code(client, adult_session, profile["id"])

    claimed = await client.post(
        "/api/v1/device-links/claim",
        json={"code": link_code["code"], **device_payload},
    )

    assert claimed.status_code == 200
    assert claimed.json()["device"]["id"] == original.json()["id"]
    assert await device_count(db_session) == 1


@pytest.mark.asyncio
async def test_claim_conflict_does_not_consume_code(client, db_session, adult_session):
    code_profile = await create_profile(client, alias="Codigo")
    other_profile = await create_profile(client, alias="Otro")
    installation = {"installation_id": "owned-elsewhere", "platform": "ANDROID"}
    assert (
        await client.post(
            f"/api/v1/profiles/{other_profile['id']}/devices",
            headers={"Authorization": f"Bearer {other_profile['bootstrap_token']}"},
            json=installation,
        )
    ).status_code == 201
    link_code = await create_link_code(client, adult_session, code_profile["id"])

    response = await client.post(
        "/api/v1/device-links/claim",
        json={"code": link_code["code"], **installation},
    )

    assert response.status_code == 409
    stored = await db_session.scalar(
        select(ProfileLinkCode).where(ProfileLinkCode.profile_id == uuid.UUID(code_profile["id"]))
    )
    assert stored is not None
    assert stored.used_at is None


@pytest.mark.asyncio
async def test_claim_rejects_profile_deactivated_after_code_creation(client, db_session, adult_session):
    profile = await create_profile(client)
    headers, _ = await adult_session(profile["id"])
    generated = await client.post(
        f"/api/v1/profiles/{profile['id']}/device-link-codes",
        headers=headers,
    )
    link_code = generated.json()
    stored_profile = await db_session.get(Profile, uuid.UUID(profile["id"]))
    assert stored_profile is not None
    stored_profile.active = False
    await db_session.commit()

    response = await client.post(
        "/api/v1/device-links/claim",
        json={
            "code": link_code["code"],
            "installation_id": "inactive-at-claim",
            "platform": "ANDROID",
        },
    )

    assert response.status_code == 410
    assert await device_count(db_session) == 0
    stored = await db_session.scalar(
        select(ProfileLinkCode).where(ProfileLinkCode.profile_id == uuid.UUID(profile["id"]))
    )
    assert stored is not None
    assert stored.used_at is None
