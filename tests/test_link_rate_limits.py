import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import OtpRateLimitEvent
from app.main import app
from app.services import otp_rate_limiter


PROFILE = "/api/v1/profiles"
ADULT_CLAIM = "/api/v1/adult/profile-links/claim"
DEVICE_CLAIM = "/api/v1/device-links/claim"


async def create_profile(client):
    return await client.post(PROFILE, json={"alias": "Test", "age": 9, "avatar": "fox"})


async def register(client, profile):
    response = await client.post(
        f"{PROFILE}/{profile['id']}/devices",
        headers={"Authorization": f"Bearer {profile['bootstrap_token']}"},
        json={"installation_id": str(uuid.uuid4()), "platform": "ANDROID"},
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def adult_headers(client):
    email = f"rate-{uuid.uuid4()}@example.com"
    requested = await client.post("/api/v1/auth/adult/otp/request", json={"email": email})
    assert requested.status_code == 202
    verified = await client.post(
        "/api/v1/auth/adult/otp/verify",
        json={"email": email, "code": requested.json()["dev_code"]},
    )
    assert verified.status_code == 200
    return {"Authorization": f"Bearer {verified.json()['access_token']}"}


@pytest.mark.asyncio
async def test_public_profile_limit_concurrency_and_expiration(client, db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "profile_create_ip_limit", 2)
    start = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
    now = [start]
    monkeypatch.setattr(otp_rate_limiter, "utc_now", lambda: now[0])
    results = await asyncio.gather(*(create_profile(client) for _ in range(5)))
    assert sorted(result.status_code for result in results) == [201, 201, 429, 429, 429]
    assert all(response.json() == {"detail": "Too many requests"}
               for response in results if response.status_code == 429)
    assert all(response.headers["Retry-After"] == "900"
               for response in results if response.status_code == 429)
    events = (await db_session.scalars(select(OtpRateLimitEvent).where(
        OtpRateLimitEvent.scope == "profile_create_ip"
    ))).all()
    assert len(events) == 2
    assert all(len(event.key_hash) == 64 for event in events)
    now[0] = start + timedelta(minutes=15)
    assert (await create_profile(client)).status_code == 201


@pytest.mark.asyncio
async def test_device_claim_failed_attempts_limited_then_legitimate_claim(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "link_claim_ip_limit", 2)
    profile_response = await create_profile(client)
    assert profile_response.status_code == 201
    profile = profile_response.json()
    adult = await adult_headers(client)
    code_response = await client.post(
        f"{PROFILE}/{profile['id']}/device-link-codes", headers=adult,
    )
    # An adult must be linked first; use the device to create and redeem its link code.
    assert code_response.status_code == 403
    device = await register(client, profile)
    adult_code = await client.post(
        f"{PROFILE}/{profile['id']}/adult-link-codes", headers=device,
    )
    assert adult_code.status_code == 201
    assert (await client.post(ADULT_CLAIM, headers=adult,
                              json={"code": adult_code.json()["code"]})).status_code == 200
    code_response = await client.post(
        f"{PROFILE}/{profile['id']}/device-link-codes", headers=adult,
    )
    assert code_response.status_code == 201
    for _ in range(2):
        invalid = await client.post(DEVICE_CLAIM, json={
            "code": "000000", "installation_id": str(uuid.uuid4()), "platform": "ANDROID",
        })
        assert invalid.status_code == 410
    blocked = await client.post(DEVICE_CLAIM, json={
        "code": code_response.json()["code"],
        "installation_id": str(uuid.uuid4()), "platform": "ANDROID",
    })
    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers["Retry-After"]) <= 900
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("203.0.113.77", 1234)),
        base_url="http://test",
    ) as other_ip:
        claimed = await other_ip.post(DEVICE_CLAIM, json={
            "code": code_response.json()["code"],
            "installation_id": str(uuid.uuid4()), "platform": "ANDROID",
        })
    assert claimed.status_code == 200
    assert claimed.json()["profile"]["id"] == profile["id"]


@pytest.mark.asyncio
async def test_adult_claim_per_account_limit_and_valid_link(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "link_claim_adult_limit", 2)
    profile_response = await create_profile(client)
    assert profile_response.status_code == 201
    profile = profile_response.json()
    device = await register(client, profile)
    code = await client.post(f"{PROFILE}/{profile['id']}/adult-link-codes", headers=device)
    assert code.status_code == 201
    adult = await adult_headers(client)
    for _ in range(2):
        invalid = await client.post(ADULT_CLAIM, headers=adult, json={"code": "000000"})
        assert invalid.status_code == 410
    blocked = await client.post(ADULT_CLAIM, headers=adult, json={"code": code.json()["code"]})
    assert blocked.status_code == 429
    assert blocked.json() == {"detail": "Too many requests"}
    other_adult = await adult_headers(client)
    accepted = await client.post(ADULT_CLAIM, headers=other_adult,
                                 json={"code": code.json()["code"]})
    assert accepted.status_code == 200
    assert accepted.json()["profile"]["id"] == profile["id"]


@pytest.mark.asyncio
async def test_concurrent_device_claims_share_persistent_ip_limit(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "link_claim_ip_limit", 2)
    attempts = await asyncio.gather(*(
        client.post(DEVICE_CLAIM, json={
            "code": "000000", "installation_id": str(uuid.uuid4()), "platform": "ANDROID",
        }) for _ in range(5)
    ))
    assert sorted(response.status_code for response in attempts) == [410, 410, 429, 429, 429]


@pytest.mark.asyncio
async def test_code_issue_limit_per_profile_preserves_existing_code(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "link_code_issue_profile_limit", 1)
    profile_response = await create_profile(client)
    assert profile_response.status_code == 201
    profile = profile_response.json()
    device = await register(client, profile)
    path = f"{PROFILE}/{profile['id']}/adult-link-codes"
    first = await client.post(path, headers=device)
    assert first.status_code == 201
    second = await client.post(path, headers=device)
    assert second.status_code == 429
    adult = await adult_headers(client)
    claimed = await client.post(ADULT_CLAIM, headers=adult,
                                json={"code": first.json()["code"]})
    assert claimed.status_code == 200
