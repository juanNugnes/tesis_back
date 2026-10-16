import base64
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.security import AdultAccessTokenCodec
from app.db.models import AdultAccount, AdultOtpChallenge, Profile
from app.repositories.adult_account_repository import AdultAccountRepository
from app.services.adult_profile_service import AdultProfileService

TEST_JWT_SECRET = "test-only-adult-auth-secret-at-least-32-characters"


async def create_profile(client, alias):
    response = await client.post(
        "/api/v1/profiles",
        json={"alias": alias, "age": 9, "avatar": "fox"},
    )
    assert response.status_code == 201
    return response.json()


async def request_otp(client, email):
    response = await client.post("/api/v1/auth/adult/otp/request", json={"email": email})
    assert response.status_code == 202
    return response


async def authenticate(client, email):
    requested = await request_otp(client, email)
    response = await client.post(
        "/api/v1/auth/adult/otp/verify",
        json={"email": email, "code": requested.json()["dev_code"]},
    )
    assert response.status_code == 200
    return response.json()


@pytest.mark.asyncio
async def test_request_otp_normalizes_email_hashes_code_and_is_generic(client, db_session, otp_delivery):
    response = await request_otp(client, "  Adulto@Example.COM  ")
    body = response.json()

    assert body["message"] == "If the address is valid, a verification code was generated."
    assert re.fullmatch(r"\d{6}", body["dev_code"])
    assert otp_delivery.codes_by_email["adulto@example.com"] == body["dev_code"]

    adult = await AdultAccountRepository(db_session).get_by_email("adulto@example.com")
    assert adult is not None
    challenge = await db_session.scalar(
        select(AdultOtpChallenge).where(AdultOtpChallenge.adult_id == adult.id)
    )
    assert challenge is not None
    assert challenge.code_hash != body["dev_code"]
    assert len(challenge.code_hash) == 64

    repeated = await request_otp(client, "adulto@example.com")
    assert repeated.json()["message"] == body["message"]
    adults = list((await db_session.execute(select(AdultAccount))).scalars().all())
    assert len(adults) == 1


@pytest.mark.asyncio
async def test_valid_otp_issues_expected_jwt_and_me_identity(client):
    token = await authenticate(client, "session@example.com")

    assert token["token_type"] == "bearer"
    assert token["expires_in"] == 3600
    encoded_payload = token["access_token"].split(".")[1]
    encoded_payload += "=" * (-len(encoded_payload) % 4)
    claims = json.loads(base64.urlsafe_b64decode(encoded_payload))
    assert set(claims) == {"sub", "type", "iat", "exp"}
    assert claims["type"] == "adult_access"
    assert "profile_id" not in claims

    response = await client.get(
        "/api/v1/auth/adult/me",
        headers={"Authorization": f"Bearer {token['access_token']}"},
    )
    assert response.status_code == 200
    assert response.json()["id"] == claims["sub"]
    assert response.json()["email"] == "session@example.com"
    assert response.json()["last_login_at"] is not None


@pytest.mark.asyncio
async def test_otp_is_single_use(client):
    requested = await request_otp(client, "single-use@example.com")
    payload = {"email": "single-use@example.com", "code": requested.json()["dev_code"]}

    assert (await client.post("/api/v1/auth/adult/otp/verify", json=payload)).status_code == 200
    second = await client.post("/api/v1/auth/adult/otp/verify", json=payload)

    assert second.status_code == 401
    assert second.json()["detail"] == "Invalid or expired verification code"


@pytest.mark.asyncio
async def test_invalid_otp_reaches_max_attempts(client, db_session):
    requested = await request_otp(client, "attempts@example.com")
    correct_code = requested.json()["dev_code"]
    wrong_code = "000000" if correct_code != "000000" else "000001"

    for _ in range(5):
        response = await client.post(
            "/api/v1/auth/adult/otp/verify",
            json={"email": "attempts@example.com", "code": wrong_code},
        )
        assert response.status_code == 401

    blocked = await client.post(
        "/api/v1/auth/adult/otp/verify",
        json={"email": "attempts@example.com", "code": correct_code},
    )
    assert blocked.status_code == 401
    challenge = await db_session.scalar(
        select(AdultOtpChallenge).order_by(AdultOtpChallenge.created_at.desc()).limit(1)
    )
    assert challenge is not None
    assert challenge.attempts == 5
    assert challenge.consumed_at is None


@pytest.mark.asyncio
async def test_expired_otp_is_rejected_without_consuming_it(client, db_session):
    requested = await request_otp(client, "expired-otp@example.com")
    challenge = await db_session.scalar(
        select(AdultOtpChallenge).order_by(AdultOtpChallenge.created_at.desc()).limit(1)
    )
    assert challenge is not None
    challenge.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.commit()

    response = await client.post(
        "/api/v1/auth/adult/otp/verify",
        json={"email": "expired-otp@example.com", "code": requested.json()["dev_code"]},
    )

    assert response.status_code == 401
    await db_session.refresh(challenge)
    assert challenge.consumed_at is None


@pytest.mark.asyncio
async def test_invalid_and_expired_jwt_return_401(client, db_session):
    invalid = await client.get(
        "/api/v1/auth/adult/me",
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert invalid.status_code == 401

    token = await authenticate(client, "expired-token@example.com")
    adult = await AdultAccountRepository(db_session).get_by_email("expired-token@example.com")
    assert adult is not None
    expired_token, _ = AdultAccessTokenCodec(TEST_JWT_SECRET, 60).issue(
        adult.id,
        expiration=timedelta(seconds=-1),
    )
    expired = await client.get(
        "/api/v1/auth/adult/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert expired.status_code == 401
    assert token["access_token"] != expired_token


@pytest.mark.asyncio
async def test_inactive_adult_is_rejected_with_existing_token(client, db_session):
    token = await authenticate(client, "inactive@example.com")
    adult = await AdultAccountRepository(db_session).get_by_email("inactive@example.com")
    assert adult is not None
    adult.active = False
    await db_session.commit()

    response = await client.get(
        "/api/v1/auth/adult/me",
        headers={"Authorization": f"Bearer {token['access_token']}"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_two_adults_are_isolated_by_profile_id(client, adult_session):
    profile_a = await create_profile(client, "Profile A")
    profile_b = await create_profile(client, "Profile B")
    headers_a, _ = await adult_session(profile_a["id"], email="adult-a@example.com")
    headers_b, _ = await adult_session(profile_b["id"], email="adult-b@example.com")

    assert (await client.get(f"/api/v1/profiles/{profile_a['id']}/usage", headers=headers_a)).status_code == 200
    assert (await client.get(f"/api/v1/profiles/{profile_b['id']}/usage", headers=headers_a)).status_code == 403
    assert (await client.get(f"/api/v1/profiles/{profile_b['id']}/usage", headers=headers_b)).status_code == 200
    assert (await client.get(f"/api/v1/profiles/{profile_a['id']}/usage", headers=headers_b)).status_code == 403


@pytest.mark.asyncio
async def test_unlinked_and_unknown_profiles_have_same_403_response(client, adult_session):
    linked = await create_profile(client, "Linked")
    unlinked = await create_profile(client, "Unlinked")
    headers, _ = await adult_session(linked["id"])

    existing_response = await client.get(
        f"/api/v1/profiles/{unlinked['id']}/usage",
        headers=headers,
    )
    unknown_response = await client.get(
        f"/api/v1/profiles/{uuid.uuid4()}/usage",
        headers=headers,
    )

    assert existing_response.status_code == 403
    assert unknown_response.status_code == 403
    assert existing_response.json() == unknown_response.json() == {"detail": "Profile access denied"}


@pytest.mark.asyncio
async def test_revoked_link_blocks_same_existing_token(client, db_session, adult_session):
    profile = await create_profile(client, "Revoked")
    headers, adult = await adult_session(profile["id"], email="revoked@example.com")
    url = f"/api/v1/profiles/{profile['id']}/usage"
    assert (await client.get(url, headers=headers)).status_code == 200

    assert await AdultProfileService(db_session).revoke_link(adult, uuid.UUID(profile["id"])) is True

    assert (await client.get(url, headers=headers)).status_code == 403


@pytest.mark.asyncio
async def test_adult_lists_only_active_linked_profiles(client, db_session, adult_session):
    profile_a = await create_profile(client, "Listed")
    profile_b = await create_profile(client, "Revoked list")
    headers, adult = await adult_session(profile_a["id"], email="list@example.com")
    profile_b_model = await db_session.get(Profile, uuid.UUID(profile_b["id"]))
    assert profile_b_model is not None
    await AdultProfileService(db_session).create_link(adult, profile_b_model)
    await AdultProfileService(db_session).revoke_link(adult, uuid.UUID(profile_b["id"]))

    response = await client.get("/api/v1/adult/profiles", headers=headers)

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [profile_a["id"]]


@pytest.mark.asyncio
async def test_device_link_code_requires_access_but_claim_stays_public(client, adult_session):
    profile_a = await create_profile(client, "Code A")
    profile_b = await create_profile(client, "Code B")
    headers, _ = await adult_session(profile_a["id"], email="code-owner@example.com")

    own = await client.post(
        f"/api/v1/profiles/{profile_a['id']}/device-link-codes",
        headers=headers,
    )
    foreign = await client.post(
        f"/api/v1/profiles/{profile_b['id']}/device-link-codes",
        headers=headers,
    )
    claim = await client.post(
        "/api/v1/device-links/claim",
        json={
            "code": own.json()["code"],
            "installation_id": "public-claim-installation",
            "platform": "ANDROID",
        },
    )

    assert own.status_code == 201
    assert foreign.status_code == 403
    assert claim.status_code == 200
    assert claim.json()["profile"]["id"] == profile_a["id"]


@pytest.mark.asyncio
async def test_protected_routes_require_their_expected_bearer(client):
    profile = await create_profile(client, "Device ingestion")

    assert (await client.get(f"/api/v1/profiles/{profile['id']}/usage")).status_code == 401
    assert (await client.get(f"/api/v1/profiles/{profile['id']}/analysis")).status_code == 401
    assert (
        await client.post(f"/api/v1/profiles/{profile['id']}/device-link-codes")
    ).status_code == 401
    usage = await client.post(
        f"/api/v1/profiles/{profile['id']}/usage",
        json={
            "snapshot_id": "00000000-0000-0000-0000-000000001005",
            "captured_at": "2026-10-05T12:00:00Z",
            "date": "2026-10-05",
            "period_start_at": "2026-10-05T00:00:00Z",
            "period_end_at": "2026-10-05T11:00:00Z",
            "timezone": "UTC",
            "duration_unit": "MINUTES",
            "measurement_method": "ANDROID_USAGE_STATS_AGGREGATES",
            "calculation_version": "1.0",
            "coverage_status": "UNKNOWN",
            "total_usage_minutes": 10,
            "night_usage_minutes": 0,
            "applications": [],
            "metric_statuses": {
                "total_usage_minutes": "MEASURED",
                "session_count": "UNAVAILABLE",
                "night_usage_minutes": "MEASURED",
                "max_session_minutes": "UNAVAILABLE",
                "unlock_count": "UNAVAILABLE",
                "applications": "MEASURED",
            },
            "night_window": {
                "source": "PROFILE_SLEEP_SCHEDULE",
                "start_local_time": "22:00:00",
                "end_local_time": "07:00:00",
            },
        },
    )
    assert usage.status_code == 401
