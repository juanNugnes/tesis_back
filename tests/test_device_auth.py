import base64
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.core.security import DeviceAccessTokenCodec
from app.db.models import Device, DeviceBootstrapToken, Profile

TEST_DEVICE_SECRET = "test-only-device-auth-secret-at-least-32-characters"


async def create_profile(client, alias="Device auth profile", *, active=True):
    response = await client.post(
        "/api/v1/profiles",
        json={"alias": alias, "age": 9, "avatar": "fox", "active": active},
    )
    assert response.status_code == 201
    return response.json()


async def register_device(client, profile, installation_id=None):
    response = await client.post(
        f"/api/v1/profiles/{profile['id']}/devices",
        headers={"Authorization": f"Bearer {profile['bootstrap_token']}"},
        json={
            "installation_id": installation_id or f"installation-{uuid.uuid4()}",
            "platform": "ANDROID",
        },
    )
    assert response.status_code == 201
    body = response.json()
    return body, {"Authorization": f"Bearer {body['access_token']}"}


async def authenticate_adult(client, email):
    requested = await client.post("/api/v1/auth/adult/otp/request", json={"email": email})
    assert requested.status_code == 202
    verified = await client.post(
        "/api/v1/auth/adult/otp/verify",
        json={"email": email, "code": requested.json()["dev_code"]},
    )
    assert verified.status_code == 200
    return {"Authorization": f"Bearer {verified.json()['access_token']}"}


async def link_adult(client, profile, device_headers, email):
    generated = await client.post(
        f"/api/v1/profiles/{profile['id']}/adult-link-codes",
        headers=device_headers,
    )
    assert generated.status_code == 201
    adult_headers = await authenticate_adult(client, email)
    claimed = await client.post(
        "/api/v1/adult/profile-links/claim",
        headers=adult_headers,
        json={"code": generated.json()["code"]},
    )
    assert claimed.status_code == 200
    return adult_headers


def usage_payload(day="2026-10-06"):
    return {
        "date": day,
        "total_usage_minutes": 15,
        "night_usage_minutes": 0,
    }


def token_claims(token):
    encoded_payload = token.split(".")[1]
    encoded_payload += "=" * (-len(encoded_payload) % 4)
    return json.loads(base64.urlsafe_b64decode(encoded_payload))


@pytest.mark.asyncio
async def test_profile_creation_issues_hashed_scoped_bootstrap(client, db_session):
    before = datetime.now(timezone.utc)
    profile = await create_profile(client)

    assert len(profile["bootstrap_token"]) >= 40
    expires_at = datetime.fromisoformat(profile["bootstrap_expires_at"])
    assert before + timedelta(minutes=9, seconds=55) < expires_at
    assert expires_at < before + timedelta(minutes=10, seconds=5)

    stored = await db_session.scalar(
        select(DeviceBootstrapToken).where(
            DeviceBootstrapToken.profile_id == uuid.UUID(profile["id"])
        )
    )
    assert stored is not None
    assert stored.token_hash != profile["bootstrap_token"]
    assert len(stored.token_hash) == 64
    assert stored.used_at is None


@pytest.mark.asyncio
async def test_bootstrap_registers_device_issues_jwt_and_is_single_use(client, db_session):
    profile = await create_profile(client)
    missing = await client.post(
        f"/api/v1/profiles/{profile['id']}/devices",
        json={"installation_id": "bootstrap-device", "platform": "ANDROID"},
    )
    device, headers = await register_device(client, profile, "bootstrap-device")

    assert missing.status_code == 401
    claims = token_claims(device["access_token"])
    assert set(claims) == {"sub", "type", "iat", "exp"}
    assert claims["sub"] == device["id"]
    assert claims["type"] == "device_access"
    assert "profile_id" not in claims
    assert device["expires_in"] == 43_200 * 60

    stored = await db_session.scalar(
        select(DeviceBootstrapToken).where(
            DeviceBootstrapToken.profile_id == uuid.UUID(profile["id"])
        )
    )
    assert stored is not None
    await db_session.refresh(stored)
    assert stored.used_at is not None

    repeated = await client.post(
        f"/api/v1/profiles/{profile['id']}/devices",
        headers={"Authorization": f"Bearer {profile['bootstrap_token']}"},
        json={"installation_id": "bootstrap-device", "platform": "ANDROID"},
    )
    assert repeated.status_code == 401
    assert (
        await client.get(f"/api/v1/profiles/{profile['id']}", headers=headers)
    ).status_code == 200


@pytest.mark.asyncio
async def test_bootstrap_is_profile_scoped_and_mismatch_does_not_consume_it(client):
    profile_a = await create_profile(client, "Profile A")
    profile_b = await create_profile(client, "Profile B")
    bootstrap_headers = {"Authorization": f"Bearer {profile_a['bootstrap_token']}"}

    rejected = await client.post(
        f"/api/v1/profiles/{profile_b['id']}/devices",
        headers=bootstrap_headers,
        json={"installation_id": "scoped-device", "platform": "ANDROID"},
    )
    accepted = await client.post(
        f"/api/v1/profiles/{profile_a['id']}/devices",
        headers=bootstrap_headers,
        json={"installation_id": "scoped-device", "platform": "ANDROID"},
    )

    assert rejected.status_code == 403
    assert accepted.status_code == 201


@pytest.mark.asyncio
async def test_expired_and_inactive_profile_bootstraps_are_not_consumed(client, db_session):
    expired_profile = await create_profile(client, "Expired bootstrap")
    expired = await db_session.scalar(
        select(DeviceBootstrapToken).where(
            DeviceBootstrapToken.profile_id == uuid.UUID(expired_profile["id"])
        )
    )
    assert expired is not None
    expired.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.commit()
    expired_response = await client.post(
        f"/api/v1/profiles/{expired_profile['id']}/devices",
        headers={"Authorization": f"Bearer {expired_profile['bootstrap_token']}"},
        json={"installation_id": "expired-bootstrap", "platform": "ANDROID"},
    )

    inactive_profile = await create_profile(client, "Inactive bootstrap")
    inactive = await db_session.get(Profile, uuid.UUID(inactive_profile["id"]))
    assert inactive is not None
    inactive.active = False
    await db_session.commit()
    inactive_response = await client.post(
        f"/api/v1/profiles/{inactive_profile['id']}/devices",
        headers={"Authorization": f"Bearer {inactive_profile['bootstrap_token']}"},
        json={"installation_id": "inactive-bootstrap", "platform": "ANDROID"},
    )

    assert expired_response.status_code == 401
    assert inactive_response.status_code == 409
    await db_session.refresh(expired)
    inactive_bootstrap = await db_session.scalar(
        select(DeviceBootstrapToken).where(
            DeviceBootstrapToken.profile_id == uuid.UUID(inactive_profile["id"])
        )
    )
    assert expired.used_at is None
    assert inactive_bootstrap is not None and inactive_bootstrap.used_at is None


@pytest.mark.asyncio
async def test_revoked_bootstrap_is_rejected_without_consumption(client, db_session):
    profile = await create_profile(client, "Revoked bootstrap")
    bootstrap = await db_session.scalar(
        select(DeviceBootstrapToken).where(
            DeviceBootstrapToken.profile_id == uuid.UUID(profile["id"])
        )
    )
    assert bootstrap is not None
    bootstrap.revoked_at = datetime.now(timezone.utc)
    await db_session.commit()

    response = await client.post(
        f"/api/v1/profiles/{profile['id']}/devices",
        headers={"Authorization": f"Bearer {profile['bootstrap_token']}"},
        json={"installation_id": "revoked-bootstrap", "platform": "ANDROID"},
    )

    assert response.status_code == 401
    await db_session.refresh(bootstrap)
    assert bootstrap.used_at is None


@pytest.mark.asyncio
async def test_device_creation_conflict_rolls_back_bootstrap_consumption(client, db_session):
    profile_a = await create_profile(client, "Conflict A")
    profile_b = await create_profile(client, "Conflict B")
    await register_device(client, profile_a, "shared-installation")
    headers_b = {"Authorization": f"Bearer {profile_b['bootstrap_token']}"}

    conflict = await client.post(
        f"/api/v1/profiles/{profile_b['id']}/devices",
        headers=headers_b,
        json={"installation_id": "shared-installation", "platform": "ANDROID"},
    )
    retry = await client.post(
        f"/api/v1/profiles/{profile_b['id']}/devices",
        headers=headers_b,
        json={"installation_id": "different-installation", "platform": "ANDROID"},
    )

    assert conflict.status_code == 409
    assert retry.status_code == 201
    count = await db_session.scalar(select(func.count()).select_from(Device))
    assert count == 2


@pytest.mark.asyncio
async def test_device_token_rejects_invalid_expired_adult_and_inactive_credentials(
    client,
    db_session,
):
    profile = await create_profile(client)
    device, headers = await register_device(client, profile)
    url = f"/api/v1/profiles/{profile['id']}/usage"
    adult_headers = await authenticate_adult(client, "not-a-device@example.com")
    expired_token, _ = DeviceAccessTokenCodec(TEST_DEVICE_SECRET, 60).issue(
        uuid.UUID(device["id"]),
        expiration=timedelta(seconds=-1),
    )

    assert (await client.post(url, json=usage_payload())).status_code == 401
    assert (
        await client.post(
            url,
            headers={"Authorization": "Bearer invalid-token"},
            json=usage_payload(),
        )
    ).status_code == 401
    assert (
        await client.post(
            url,
            headers={"Authorization": f"Bearer {expired_token}"},
            json=usage_payload(),
        )
    ).status_code == 401
    assert (
        await client.post(url, headers=adult_headers, json=usage_payload())
    ).status_code == 401
    assert (await client.get("/api/v1/auth/adult/me", headers=headers)).status_code == 401

    stored_device = await db_session.get(Device, uuid.UUID(device["id"]))
    assert stored_device is not None
    stored_device.active = False
    await db_session.commit()
    assert (
        await client.post(url, headers=headers, json=usage_payload())
    ).status_code == 401


@pytest.mark.asyncio
async def test_inactive_profile_invalidates_existing_device_token(client, db_session):
    profile = await create_profile(client, "Inactive after registration")
    _, headers = await register_device(client, profile)
    stored_profile = await db_session.get(Profile, uuid.UUID(profile["id"]))
    assert stored_profile is not None
    stored_profile.active = False
    await db_session.commit()

    response = await client.get(f"/api/v1/profiles/{profile['id']}", headers=headers)

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_device_is_authorized_only_for_its_persisted_profile(client):
    profile_a = await create_profile(client, "Usage A")
    profile_b = await create_profile(client, "Usage B")
    _, headers_a = await register_device(client, profile_a)
    _, headers_b = await register_device(client, profile_b)

    own_a = await client.post(
        f"/api/v1/profiles/{profile_a['id']}/usage",
        headers=headers_a,
        json=usage_payload("2026-10-06"),
    )
    foreign_a = await client.post(
        f"/api/v1/profiles/{profile_b['id']}/usage",
        headers=headers_a,
        json=usage_payload("2026-10-07"),
    )
    own_b = await client.post(
        f"/api/v1/profiles/{profile_b['id']}/usage",
        headers=headers_b,
        json=usage_payload("2026-10-07"),
    )
    foreign_b = await client.post(
        f"/api/v1/profiles/{profile_a['id']}/usage",
        headers=headers_b,
        json=usage_payload("2026-10-08"),
    )

    assert own_a.status_code == own_b.status_code == 201
    assert foreign_a.status_code == foreign_b.status_code == 403


@pytest.mark.asyncio
async def test_adult_link_code_generation_is_device_only_and_profile_scoped(client):
    profile_a = await create_profile(client, "Link A")
    profile_b = await create_profile(client, "Link B")
    _, headers_a = await register_device(client, profile_a)
    adult_headers = await authenticate_adult(client, "adult-on-device-route@example.com")

    own = await client.post(
        f"/api/v1/profiles/{profile_a['id']}/adult-link-codes",
        headers=headers_a,
    )
    foreign = await client.post(
        f"/api/v1/profiles/{profile_b['id']}/adult-link-codes",
        headers=headers_a,
    )
    missing = await client.post(f"/api/v1/profiles/{profile_a['id']}/adult-link-codes")
    adult = await client.post(
        f"/api/v1/profiles/{profile_a['id']}/adult-link-codes",
        headers=adult_headers,
    )

    assert own.status_code == 201
    assert foreign.status_code == 403
    assert missing.status_code == adult.status_code == 401


@pytest.mark.asyncio
async def test_device_link_claim_issues_token_for_new_and_existing_device(
    client,
    db_session,
    adult_session,
):
    profile = await create_profile(client, "Recovery")
    original, _ = await register_device(client, profile, "recover-existing")
    adult_headers, _ = await adult_session(profile["id"], email="recovery@example.com")

    first_code = await client.post(
        f"/api/v1/profiles/{profile['id']}/device-link-codes",
        headers=adult_headers,
    )
    recovered = await client.post(
        "/api/v1/device-links/claim",
        json={
            "code": first_code.json()["code"],
            "installation_id": "recover-existing",
            "platform": "ANDROID",
        },
    )
    recovered_headers = {"Authorization": f"Bearer {recovered.json()['access_token']}"}

    second_code = await client.post(
        f"/api/v1/profiles/{profile['id']}/device-link-codes",
        headers=adult_headers,
    )
    added = await client.post(
        "/api/v1/device-links/claim",
        json={
            "code": second_code.json()["code"],
            "installation_id": "recover-new",
            "platform": "ANDROID",
        },
    )

    assert recovered.status_code == added.status_code == 200
    assert recovered.json()["device"]["id"] == original["id"]
    assert recovered.json()["access_token"]
    assert added.json()["access_token"]
    assert (
        await client.get(f"/api/v1/profiles/{profile['id']}", headers=recovered_headers)
    ).status_code == 200
    assert await db_session.scalar(select(func.count()).select_from(Device)) == 2


@pytest.mark.asyncio
async def test_shared_routes_accept_own_adult_or_device_but_context_is_adult_only(client):
    profile = await create_profile(client, "Shared A")
    foreign = await create_profile(client, "Shared B")
    _, device_headers = await register_device(client, profile)
    adult_headers = await link_adult(
        client,
        profile,
        device_headers,
        "shared-access@example.com",
    )

    for headers in (device_headers, adult_headers):
        assert (
            await client.get(f"/api/v1/profiles/{profile['id']}", headers=headers)
        ).status_code == 200
        assert (
            await client.get(
                f"/api/v1/profiles/{profile['id']}/settings",
                headers=headers,
            )
        ).status_code == 200
        assert (
            await client.put(
                f"/api/v1/profiles/{profile['id']}/activity-preferences",
                headers=headers,
                json={"interests": ["MUSIC"]},
            )
        ).status_code == 200

    assert (
        await client.get(f"/api/v1/profiles/{foreign['id']}", headers=device_headers)
    ).status_code == 403
    assert (
        await client.get(f"/api/v1/profiles/{foreign['id']}", headers=adult_headers)
    ).status_code == 403
    assert (
        await client.post(
            f"/api/v1/profiles/{profile['id']}/contexts",
            headers=device_headers,
            json={"type": "VACATION", "start_date": "2026-10-01"},
        )
    ).status_code == 401
    assert (
        await client.post(
            f"/api/v1/profiles/{profile['id']}/contexts",
            headers=adult_headers,
            json={"type": "VACATION", "start_date": "2026-10-01"},
        )
    ).status_code == 201


def test_device_security_openapi_contract():
    from app.main import app

    schema = app.openapi()
    schemes = schema["components"]["securitySchemes"]
    paths = schema["paths"]

    assert set(schemes) == {"AdultBearer", "DeviceBearer", "DeviceBootstrapBearer"}
    assert paths["/api/v1/profiles/{profile_id}/devices"]["post"]["security"] == [
        {"DeviceBootstrapBearer": []}
    ]
    assert paths["/api/v1/profiles/{profile_id}/usage"]["post"]["security"] == [
        {"DeviceBearer": []}
    ]
    assert paths["/api/v1/profiles/{profile_id}/adult-link-codes"]["post"][
        "security"
    ] == [{"DeviceBearer": []}]
    assert paths["/api/v1/profiles/{profile_id}"]["get"]["security"] == [
        {"AdultBearer": []},
        {"DeviceBearer": []},
    ]
    assert "security" not in paths["/api/v1/profiles"]["post"]
    assert "security" not in paths["/api/v1/device-links/claim"]["post"]
