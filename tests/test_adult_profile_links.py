import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.security import AdultAccessTokenCodec
from app.db.models import (
    AdultProfileLink,
    AuditEvent,
    AuditEventType,
    Consent,
    Profile,
    ProfileLinkCode,
    RepresentativeVerification,
    RepresentativeVerificationStatus,
)
from app.repositories.adult_account_repository import AdultAccountRepository
from app.repositories.profile_repository import ProfileRepository
from app.services.adult_profile_service import AdultProfileService
from app.services.profile_link_service import ProfileLinkService
from app.services.representative_verification_service import RepresentativeVerificationService

TEST_JWT_SECRET = "test-only-adult-auth-secret-at-least-32-characters"
DEVICE_HEADERS_BY_PROFILE = {}


async def create_profile(client, *, alias="Perfil vinculable", active=True):
    response = await client.post(
        "/api/v1/profiles",
        json={"alias": alias, "age": 9, "avatar": "fox", "active": active},
    )
    assert response.status_code == 201
    profile = response.json()
    if active:
        registered = await client.post(
            f"/api/v1/profiles/{profile['id']}/devices",
            headers={"Authorization": f"Bearer {profile['bootstrap_token']}"},
            json={"installation_id": f"adult-link-{uuid.uuid4()}", "platform": "ANDROID"},
        )
        assert registered.status_code == 201
        DEVICE_HEADERS_BY_PROFILE[profile["id"]] = {
            "Authorization": f"Bearer {registered.json()['access_token']}"
        }
    return profile


async def authenticate(client, db_session, email):
    requested = await client.post("/api/v1/auth/adult/otp/request", json={"email": email})
    assert requested.status_code == 202
    verified = await client.post(
        "/api/v1/auth/adult/otp/verify",
        json={"email": email, "code": requested.json()["dev_code"]},
    )
    assert verified.status_code == 200
    adult = await AdultAccountRepository(db_session).get_by_email(email)
    assert adult is not None
    return {"Authorization": f"Bearer {verified.json()['access_token']}"}, adult


async def generate_adult_link_code(client, profile_id):
    response = await client.post(
        f"/api/v1/profiles/{profile_id}/adult-link-codes",
        headers=DEVICE_HEADERS_BY_PROFILE[profile_id],
    )
    assert response.status_code == 201
    return response.json()


async def get_stored_code(db_session, profile_id, purpose="ADULT_PROFILE_LINK"):
    return await db_session.scalar(
        select(ProfileLinkCode)
        .where(
            ProfileLinkCode.profile_id == uuid.UUID(str(profile_id)),
            ProfileLinkCode.purpose == purpose,
        )
        .order_by(ProfileLinkCode.created_at.desc())
        .limit(1)
    )


def set_auto_approval(monkeypatch, enabled, email="auto-approve@example.com"):
    monkeypatch.setattr(
        get_settings(),
        "auto_approve_representative_verification",
        enabled,
    )
    monkeypatch.setattr(
        get_settings(),
        "auto_approve_representative_email_allowlist",
        f"{email},second-test@example.com",
    )


@pytest.mark.asyncio
async def test_generate_adult_link_code_hashes_it_and_sets_expiration(client, db_session):
    profile = await create_profile(client)
    before = datetime.now(timezone.utc)

    response = await client.post(
        f"/api/v1/profiles/{profile['id']}/adult-link-codes",
        headers=DEVICE_HEADERS_BY_PROFILE[profile["id"]],
    )

    assert response.status_code == 201
    body = response.json()
    assert re.fullmatch(r"\d{6}", body["code"])
    assert body["purpose"] == "ADULT_PROFILE_LINK"
    expires_at = datetime.fromisoformat(body["expires_at"])
    assert before + timedelta(minutes=9, seconds=55) < expires_at
    assert expires_at < before + timedelta(minutes=10, seconds=5)

    stored = await get_stored_code(db_session, profile["id"])
    assert stored is not None
    assert stored.code_hash != body["code"]
    assert len(stored.code_hash) == 64
    assert stored.purpose == "ADULT_PROFILE_LINK"


@pytest.mark.asyncio
async def test_generate_adult_link_code_validates_profile(client):
    own = await create_profile(client)
    missing = await client.post(
        f"/api/v1/profiles/{uuid.uuid4()}/adult-link-codes",
        headers=DEVICE_HEADERS_BY_PROFILE[own["id"]],
    )
    rejected = await client.post(
        f"/api/v1/profiles/{own['id']}/adult-link-codes",
        json={},
    )

    assert missing.status_code == 403
    assert rejected.status_code == 401


@pytest.mark.asyncio
async def test_generate_adult_link_code_retries_collision(client, db_session):
    profile_data = await create_profile(client)
    profile = await ProfileRepository(db_session).get(uuid.UUID(profile_data["id"]))
    assert profile is not None
    generated_values = iter(["123456", "123456", "654321"])
    service = ProfileLinkService(
        db_session,
        "test-hash-secret",
        code_generator=lambda: next(generated_values),
    )

    first = await service.generate_adult_profile_link_code(profile)
    second = await service.generate_adult_profile_link_code(profile)

    assert first.code == "123456"
    assert second.code == "654321"
    assert first.purpose == second.purpose == "ADULT_PROFILE_LINK"


@pytest.mark.asyncio
async def test_claim_creates_link_lists_profile_and_authorizes_same_token(client, db_session):
    profile = await create_profile(client)
    code = await generate_adult_link_code(client, profile["id"])
    headers, adult = await authenticate(client, db_session, "claim@example.com")

    response = await client.post(
        "/api/v1/adult/profile-links/claim",
        headers=headers,
        json={"code": code["code"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["profile"]["id"] == profile["id"]
    assert body["link"]["profile_id"] == profile["id"]
    assert body["link"]["active"] is True
    assert "adult_id" not in body["link"]
    link = await db_session.scalar(
        select(AdultProfileLink).where(AdultProfileLink.adult_id == adult.id)
    )
    assert link is not None
    stored = await get_stored_code(db_session, profile["id"])
    assert stored is not None
    await db_session.refresh(stored)
    assert stored.used_at is not None

    listed = await client.get("/api/v1/adult/profiles", headers=headers)
    protected = await client.get(f"/api/v1/profiles/{profile['id']}/usage", headers=headers)
    assert [item["id"] for item in listed.json()] == [profile["id"]]
    assert protected.status_code == 200


@pytest.mark.asyncio
async def test_claim_schema_rejects_adult_and_profile_ids(client, db_session):
    profile = await create_profile(client)
    code = await generate_adult_link_code(client, profile["id"])
    headers, _ = await authenticate(client, db_session, "strict-body@example.com")

    response = await client.post(
        "/api/v1/adult/profile-links/claim",
        headers=headers,
        json={
            "code": code["code"],
            "adult_id": str(uuid.uuid4()),
            "profile_id": str(uuid.uuid4()),
        },
    )

    assert response.status_code == 422
    stored = await get_stored_code(db_session, profile["id"])
    assert stored is not None
    assert stored.used_at is None


@pytest.mark.asyncio
async def test_only_first_adult_can_claim_a_code(client, db_session):
    profile = await create_profile(client)
    code = await generate_adult_link_code(client, profile["id"])
    headers_a, adult_a = await authenticate(client, db_session, "adult-a-claim@example.com")
    headers_b, adult_b = await authenticate(client, db_session, "adult-b-claim@example.com")

    first = await client.post(
        "/api/v1/adult/profile-links/claim", headers=headers_a, json={"code": code["code"]}
    )
    second = await client.post(
        "/api/v1/adult/profile-links/claim", headers=headers_b, json={"code": code["code"]}
    )

    assert first.status_code == 200
    assert second.status_code == 410
    links = list((await db_session.execute(select(AdultProfileLink))).scalars().all())
    assert [(link.adult_id, link.profile_id) for link in links] == [
        (adult_a.id, uuid.UUID(profile["id"]))
    ]
    assert all(link.adult_id != adult_b.id for link in links)


@pytest.mark.asyncio
async def test_claim_is_idempotent_for_existing_active_link(client, db_session):
    profile = await create_profile(client)
    headers, adult = await authenticate(client, db_session, "idempotent@example.com")
    first_code = await generate_adult_link_code(client, profile["id"])
    first = await client.post(
        "/api/v1/adult/profile-links/claim",
        headers=headers,
        json={"code": first_code["code"]},
    )
    second_code = await generate_adult_link_code(client, profile["id"])
    second = await client.post(
        "/api/v1/adult/profile-links/claim",
        headers=headers,
        json={"code": second_code["code"]},
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["link"]["id"] == second.json()["link"]["id"]
    count = await db_session.scalar(
        select(func.count())
        .select_from(AdultProfileLink)
        .where(AdultProfileLink.adult_id == adult.id)
    )
    assert count == 1
    second_stored = await get_stored_code(db_session, profile["id"])
    assert second_stored is not None
    await db_session.refresh(second_stored)
    assert second_stored.used_at is not None


@pytest.mark.asyncio
async def test_revoked_link_is_not_reactivated_or_code_consumed(client, db_session):
    profile = await create_profile(client)
    headers, adult = await authenticate(client, db_session, "revoked-claim@example.com")
    first_code = await generate_adult_link_code(client, profile["id"])
    assert (
        await client.post(
            "/api/v1/adult/profile-links/claim",
            headers=headers,
            json={"code": first_code["code"]},
        )
    ).status_code == 200
    assert await AdultProfileService(db_session).revoke_link(
        adult,
        uuid.UUID(profile["id"]),
    )
    second_code = await generate_adult_link_code(client, profile["id"])

    response = await client.post(
        "/api/v1/adult/profile-links/claim",
        headers=headers,
        json={"code": second_code["code"]},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Profile link cannot be reactivated automatically"
    link = await db_session.scalar(
        select(AdultProfileLink).where(AdultProfileLink.adult_id == adult.id)
    )
    await db_session.refresh(link)
    assert link.active is False
    assert link.revoked_at is not None
    second_stored = await get_stored_code(db_session, profile["id"])
    assert second_stored is not None
    await db_session.refresh(second_stored)
    assert second_stored.used_at is None


@pytest.mark.asyncio
async def test_expired_adult_link_code_is_rejected_without_link(client, db_session):
    profile = await create_profile(client)
    code = await generate_adult_link_code(client, profile["id"])
    stored = await get_stored_code(db_session, profile["id"])
    assert stored is not None
    stored.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.commit()
    headers, adult = await authenticate(client, db_session, "expired-link@example.com")

    response = await client.post(
        "/api/v1/adult/profile-links/claim",
        headers=headers,
        json={"code": code["code"]},
    )

    assert response.status_code == 410
    assert response.json()["detail"] == "Invalid or expired link code"
    assert await db_session.scalar(
        select(AdultProfileLink).where(AdultProfileLink.adult_id == adult.id)
    ) is None
    await db_session.refresh(stored)
    assert stored.used_at is None


@pytest.mark.asyncio
async def test_revoked_adult_link_code_is_rejected_without_link(client, db_session):
    profile = await create_profile(client)
    code = await generate_adult_link_code(client, profile["id"])
    stored = await get_stored_code(db_session, profile["id"])
    assert stored is not None
    stored.revoked_at = datetime.now(timezone.utc)
    await db_session.commit()
    headers, adult = await authenticate(client, db_session, "revoked-code@example.com")

    response = await client.post(
        "/api/v1/adult/profile-links/claim",
        headers=headers,
        json={"code": code["code"]},
    )

    assert response.status_code == 410
    assert response.json()["detail"] == "Invalid or expired link code"
    assert await db_session.scalar(
        select(AdultProfileLink).where(AdultProfileLink.adult_id == adult.id)
    ) is None
    await db_session.refresh(stored)
    assert stored.used_at is None


@pytest.mark.asyncio
async def test_device_link_code_is_rejected_by_adult_claim(client, db_session, adult_session):
    profile = await create_profile(client)
    owner_headers, _ = await adult_session(profile["id"], email="device-code-owner@example.com")
    generated = await client.post(
        f"/api/v1/profiles/{profile['id']}/device-link-codes",
        headers=owner_headers,
    )
    headers, adult = await authenticate(client, db_session, "other-adult@example.com")

    response = await client.post(
        "/api/v1/adult/profile-links/claim",
        headers=headers,
        json={"code": generated.json()["code"]},
    )

    assert response.status_code == 410
    assert await db_session.scalar(
        select(AdultProfileLink).where(AdultProfileLink.adult_id == adult.id)
    ) is None


@pytest.mark.asyncio
async def test_adult_claim_requires_valid_token_without_consuming_code(client, db_session):
    profile = await create_profile(client)
    code = await generate_adult_link_code(client, profile["id"])
    _, adult = await authenticate(client, db_session, "expired-claim-token@example.com")
    expired_token, _ = AdultAccessTokenCodec(TEST_JWT_SECRET, 60).issue(
        adult.id,
        expiration=timedelta(seconds=-1),
    )

    missing = await client.post(
        "/api/v1/adult/profile-links/claim",
        json={"code": code["code"]},
    )
    invalid = await client.post(
        "/api/v1/adult/profile-links/claim",
        headers={"Authorization": "Bearer invalid-token"},
        json={"code": code["code"]},
    )
    expired = await client.post(
        "/api/v1/adult/profile-links/claim",
        headers={"Authorization": f"Bearer {expired_token}"},
        json={"code": code["code"]},
    )

    assert missing.status_code == invalid.status_code == expired.status_code == 401
    stored = await get_stored_code(db_session, profile["id"])
    assert stored is not None
    assert stored.used_at is None


@pytest.mark.asyncio
async def test_profile_deactivated_after_generation_rejects_claim(client, db_session):
    profile_data = await create_profile(client)
    code = await generate_adult_link_code(client, profile_data["id"])
    profile = await db_session.get(Profile, uuid.UUID(profile_data["id"]))
    assert profile is not None
    profile.active = False
    await db_session.commit()
    headers, adult = await authenticate(client, db_session, "inactive-profile@example.com")

    response = await client.post(
        "/api/v1/adult/profile-links/claim",
        headers=headers,
        json={"code": code["code"]},
    )

    assert response.status_code == 410
    assert await db_session.scalar(
        select(AdultProfileLink).where(AdultProfileLink.adult_id == adult.id)
    ) is None
    stored = await get_stored_code(db_session, profile_data["id"])
    assert stored is not None
    assert stored.used_at is None


@pytest.mark.asyncio
async def test_enabled_flag_auto_approves_after_claim_and_audits_without_consent(
    client,
    db_session,
    monkeypatch,
):
    set_auto_approval(monkeypatch, True)
    profile = await create_profile(client)
    code = await generate_adult_link_code(client, profile["id"])
    headers, adult = await authenticate(client, db_session, "auto-approve@example.com")

    response = await client.post(
        "/api/v1/adult/profile-links/claim",
        headers=headers,
        json={"code": code["code"]},
    )
    second_code = await generate_adult_link_code(client, profile["id"])
    repeated = await client.post(
        "/api/v1/adult/profile-links/claim",
        headers=headers,
        json={"code": second_code["code"]},
    )

    assert response.status_code == 200
    assert repeated.status_code == 200
    verification = await db_session.scalar(
        select(RepresentativeVerification).where(
            RepresentativeVerification.adult_id == adult.id,
            RepresentativeVerification.profile_id == uuid.UUID(profile["id"]),
        )
    )
    assert verification is not None
    assert verification.status == RepresentativeVerificationStatus.APPROVED
    assert verification.verified_at is not None
    assert verification.verified_by == "automatic-mvp-allowlist"
    approval_events = await db_session.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(
            AuditEvent.profile_id == uuid.UUID(profile["id"]),
            AuditEvent.event_type
            == AuditEventType.REPRESENTATIVE_VERIFICATION_APPROVED,
        )
    )
    event = await db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.profile_id == uuid.UUID(profile["id"]),
            AuditEvent.event_type
            == AuditEventType.REPRESENTATIVE_VERIFICATION_APPROVED,
        )
    )
    assert approval_events == 1
    assert event is not None
    assert event.event_metadata == {"verified_by": "automatic-mvp-allowlist"}
    assert await db_session.scalar(select(func.count()).select_from(Consent)) == 0
    granted = await client.post(
        f"/api/v1/profiles/{profile['id']}/consents",
        headers=headers,
        json={
            "privacy_notice_version": "1.0",
            "purposes": ["USAGE_MONITORING"],
        },
    )
    assert granted.status_code == 201


@pytest.mark.asyncio
async def test_enabled_flag_promotes_existing_pending_without_duplicate(
    client,
    db_session,
    monkeypatch,
):
    set_auto_approval(monkeypatch, True, "pending-auto@example.com")
    profile_data = await create_profile(client)
    code = await generate_adult_link_code(client, profile_data["id"])
    headers, adult = await authenticate(client, db_session, "pending-auto@example.com")
    profile = await ProfileRepository(db_session).get(uuid.UUID(profile_data["id"]))
    assert profile is not None
    await AdultProfileService(db_session).create_link(adult, profile)
    pending = await db_session.scalar(
        select(RepresentativeVerification).where(
            RepresentativeVerification.adult_id == adult.id,
            RepresentativeVerification.profile_id == profile.id,
        )
    )
    assert pending is not None
    pending_id = pending.id
    adult_id = adult.id
    profile_id = profile.id

    response = await client.post(
        "/api/v1/adult/profile-links/claim",
        headers=headers,
        json={"code": code["code"]},
    )

    assert response.status_code == 200
    db_session.expire_all()
    verification = await db_session.get(RepresentativeVerification, pending_id)
    assert verification is not None
    assert verification.status == RepresentativeVerificationStatus.APPROVED
    assert verification.verified_by == "automatic-mvp-allowlist"
    count = await db_session.scalar(
        select(func.count())
        .select_from(RepresentativeVerification)
        .where(
            RepresentativeVerification.adult_id == adult_id,
            RepresentativeVerification.profile_id == profile_id,
        )
    )
    assert count == 1


@pytest.mark.asyncio
async def test_enabled_flag_never_overwrites_rejected_verification(
    client,
    db_session,
    monkeypatch,
):
    set_auto_approval(monkeypatch, True, "rejected-auto@example.com")
    profile_data = await create_profile(client)
    code = await generate_adult_link_code(client, profile_data["id"])
    headers, adult = await authenticate(client, db_session, "rejected-auto@example.com")
    profile = await ProfileRepository(db_session).get(uuid.UUID(profile_data["id"]))
    assert profile is not None
    await AdultProfileService(db_session).create_link(adult, profile)
    rejected = await RepresentativeVerificationService(db_session).reject(
        adult.id,
        profile.id,
        verified_by="manual-review",
    )
    rejected_id = rejected.id
    profile_id = profile.id

    response = await client.post(
        "/api/v1/adult/profile-links/claim",
        headers=headers,
        json={"code": code["code"]},
    )

    assert response.status_code == 200
    db_session.expire_all()
    verification = await db_session.get(RepresentativeVerification, rejected_id)
    assert verification is not None
    assert verification.status == RepresentativeVerificationStatus.REJECTED
    assert verification.verified_by == "manual-review"
    approved_events = await db_session.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(
            AuditEvent.profile_id == profile_id,
            AuditEvent.event_type
            == AuditEventType.REPRESENTATIVE_VERIFICATION_APPROVED,
        )
    )
    assert approved_events == 0


@pytest.mark.asyncio
async def test_enabled_flag_does_not_approve_email_outside_allowlist_after_claim(
    client, db_session, monkeypatch,
):
    set_auto_approval(monkeypatch, True, "allowed@example.com")
    profile = await create_profile(client)
    code = await generate_adult_link_code(client, profile["id"])
    headers, adult = await authenticate(client, db_session, "outside@example.com")

    response = await client.post(
        "/api/v1/adult/profile-links/claim", headers=headers, json={"code": code["code"]},
    )

    assert response.status_code == 200
    verification = await db_session.scalar(select(RepresentativeVerification).where(
        RepresentativeVerification.adult_id == adult.id,
        RepresentativeVerification.profile_id == uuid.UUID(profile["id"]),
    ))
    assert verification is not None
    assert verification.status == RepresentativeVerificationStatus.PENDING
    assert await db_session.scalar(select(func.count()).select_from(AuditEvent).where(
        AuditEvent.event_type == AuditEventType.REPRESENTATIVE_VERIFICATION_APPROVED,
    )) == 0


@pytest.mark.asyncio
async def test_disabled_flag_preserves_pending_manual_flow(
    client,
    db_session,
    monkeypatch,
):
    set_auto_approval(monkeypatch, False)
    profile = await create_profile(client)
    code = await generate_adult_link_code(client, profile["id"])
    headers, adult = await authenticate(client, db_session, "manual-flow@example.com")

    response = await client.post(
        "/api/v1/adult/profile-links/claim",
        headers=headers,
        json={"code": code["code"]},
    )

    assert response.status_code == 200
    verification = await db_session.scalar(
        select(RepresentativeVerification).where(
            RepresentativeVerification.adult_id == adult.id,
            RepresentativeVerification.profile_id == uuid.UUID(profile["id"]),
        )
    )
    assert verification is not None
    assert verification.status == RepresentativeVerificationStatus.PENDING
    assert verification.verified_by is None
    assert await db_session.scalar(select(func.count()).select_from(AuditEvent)) == 0


@pytest.mark.asyncio
async def test_otp_and_device_link_do_not_trigger_auto_approval(
    client,
    db_session,
    monkeypatch,
):
    set_auto_approval(monkeypatch, True, "non-trigger@example.com")
    headers, adult = await authenticate(client, db_session, "non-trigger@example.com")
    assert await db_session.scalar(
        select(func.count()).select_from(RepresentativeVerification)
    ) == 0

    profile_data = await create_profile(client)
    profile = await ProfileRepository(db_session).get(uuid.UUID(profile_data["id"]))
    assert profile is not None
    await AdultProfileService(db_session).create_link(adult, profile)
    verification = await db_session.scalar(
        select(RepresentativeVerification).where(
            RepresentativeVerification.adult_id == adult.id,
            RepresentativeVerification.profile_id == profile.id,
        )
    )
    assert verification is not None
    assert verification.status == RepresentativeVerificationStatus.PENDING
    verification_id = verification.id

    generated = await client.post(
        f"/api/v1/profiles/{profile.id}/device-link-codes",
        headers=headers,
    )
    claimed = await client.post(
        "/api/v1/device-links/claim",
        json={
            "code": generated.json()["code"],
            "installation_id": f"non-trigger-{uuid.uuid4()}",
            "platform": "ANDROID",
        },
    )

    assert generated.status_code == 201
    assert claimed.status_code == 200
    db_session.expire_all()
    unchanged = await db_session.get(RepresentativeVerification, verification_id)
    assert unchanged is not None
    assert unchanged.status == RepresentativeVerificationStatus.PENDING
    assert await db_session.scalar(
        select(func.count()).select_from(AuditEvent)
    ) == 0


def test_adult_profile_link_openapi_contract():
    from app.main import app

    schema = app.openapi()
    generation = schema["paths"]["/api/v1/profiles/{profile_id}/adult-link-codes"]["post"]
    claim = schema["paths"]["/api/v1/adult/profile-links/claim"]["post"]
    request_schema = schema["components"]["schemas"]["AdultProfileLinkClaimRequest"]

    assert generation["security"] == [{"DeviceBearer": []}]
    assert claim["security"] == [{"AdultBearer": []}]
    assert request_schema["properties"].keys() == {"code"}
    assert request_schema["additionalProperties"] is False
