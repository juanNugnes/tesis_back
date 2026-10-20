import uuid

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.models import (
    AdultProfileLink, AuditEvent, AuditEventType, Consent, RepresentativeVerification,
    RepresentativeVerificationStatus,
)
from app.repositories.adult_account_repository import AdultAccountRepository
from app.repositories.adult_profile_link_repository import AdultProfileLinkRepository
from app.repositories.profile_repository import ProfileRepository
from app.services.adult_profile_service import AdultProfileService
from app.services.representative_auto_approval import auto_approval_allowed
from app.services.representative_verification_service import RepresentativeVerificationService


EMAIL_ONE = "pilot-one@example.com"
EMAIL_TWO = "pilot-two@example.com"


def enable(monkeypatch, allowlist=f"{EMAIL_ONE},{EMAIL_TWO}"):
    monkeypatch.setattr(get_settings(), "auto_approve_representative_verification", True)
    monkeypatch.setattr(get_settings(), "auto_approve_representative_email_allowlist", allowlist)


async def profile(client):
    response = await client.post(
        "/api/v1/profiles", json={"alias": "Pilot test", "age": 9, "avatar": "fox"},
    )
    assert response.status_code == 201
    return response.json()


async def otp_request(client, email):
    response = await client.post("/api/v1/auth/adult/otp/request", json={"email": email})
    assert response.status_code == 202
    return response.json()["dev_code"]


async def otp_verify(client, email, code):
    return await client.post(
        "/api/v1/auth/adult/otp/verify", json={"email": email, "code": code},
    )


async def link_existing(db_session, adult, profile_id):
    linked_profile = await ProfileRepository(db_session).get(uuid.UUID(profile_id))
    assert linked_profile is not None
    await AdultProfileService(db_session).create_link(adult, linked_profile)
    return linked_profile


async def verification(db_session, adult_id, profile_id):
    result = await db_session.scalar(select(RepresentativeVerification).where(
        RepresentativeVerification.adult_id == adult_id,
        RepresentativeVerification.profile_id == profile_id,
    ))
    if result is not None:
        await db_session.refresh(result)
    return result


async def approval_count(db_session, profile_id):
    return await db_session.scalar(select(func.count()).select_from(AuditEvent).where(
        AuditEvent.profile_id == profile_id,
        AuditEvent.event_type == AuditEventType.REPRESENTATIVE_VERIFICATION_APPROVED,
    ))


@pytest.mark.asyncio
async def test_allowed_otp_approves_only_existing_active_link_and_leaves_consent_pending(
    client, db_session, monkeypatch,
):
    enable(monkeypatch, "  PILOT-ONE@EXAMPLE.COM , pilot-two@example.com  ")
    linked = await profile(client)
    unrelated = await profile(client)
    code = await otp_request(client, EMAIL_ONE)
    adult = await AdultAccountRepository(db_session).get_by_email(EMAIL_ONE)
    assert adult is not None
    own_profile = await link_existing(db_session, adult, linked["id"])

    response = await otp_verify(client, EMAIL_ONE, code)

    assert response.status_code == 200
    own = await verification(db_session, adult.id, own_profile.id)
    assert own is not None and own.status == RepresentativeVerificationStatus.APPROVED
    assert own.verified_by == "automatic-mvp-allowlist"
    assert await verification(db_session, adult.id, uuid.UUID(unrelated["id"])) is None
    assert await approval_count(db_session, own_profile.id) == 1
    assert await db_session.scalar(select(func.count()).select_from(AdultProfileLink)) == 1
    assert await db_session.scalar(select(func.count()).select_from(Consent)) == 0

    await RepresentativeVerificationService(db_session).approve_automatically(adult.id, own_profile.id)
    assert await approval_count(db_session, own_profile.id) == 1


@pytest.mark.asyncio
async def test_allowed_otp_without_link_does_not_create_link_or_verification(client, db_session, monkeypatch):
    enable(monkeypatch)
    unlinked = await profile(client)
    code = await otp_request(client, EMAIL_ONE)
    assert (await otp_verify(client, EMAIL_ONE, code)).status_code == 200
    assert await db_session.scalar(select(func.count()).select_from(AdultProfileLink)) == 0
    assert await db_session.scalar(select(func.count()).select_from(RepresentativeVerification)) == 0
    assert await approval_count(db_session, uuid.UUID(unlinked["id"])) == 0


@pytest.mark.asyncio
async def test_allowed_otp_does_not_create_missing_verification(client, db_session, monkeypatch):
    enable(monkeypatch)
    linked = await profile(client)
    code = await otp_request(client, EMAIL_ONE)
    adult = await AdultAccountRepository(db_session).get_by_email(EMAIL_ONE)
    assert adult is not None
    await AdultProfileLinkRepository(db_session).create_link(adult.id, uuid.UUID(linked["id"]))
    await db_session.commit()

    assert (await otp_verify(client, EMAIL_ONE, code)).status_code == 200
    assert await verification(db_session, adult.id, uuid.UUID(linked["id"])) is None
    assert await approval_count(db_session, uuid.UUID(linked["id"])) == 0


@pytest.mark.asyncio
async def test_wrong_otp_does_not_approve_then_correct_otp_does(client, db_session, monkeypatch):
    enable(monkeypatch)
    linked = await profile(client)
    code = await otp_request(client, EMAIL_ONE)
    adult = await AdultAccountRepository(db_session).get_by_email(EMAIL_ONE)
    assert adult is not None
    own_profile = await link_existing(db_session, adult, linked["id"])
    wrong = "000000" if code != "000000" else "000001"
    assert (await otp_verify(client, EMAIL_ONE, wrong)).status_code == 401
    pending = await verification(db_session, adult.id, own_profile.id)
    assert pending is not None and pending.status == RepresentativeVerificationStatus.PENDING
    assert await approval_count(db_session, own_profile.id) == 0
    assert (await otp_verify(client, EMAIL_ONE, code)).status_code == 200
    approved = await verification(db_session, adult.id, own_profile.id)
    assert approved is not None and approved.status == RepresentativeVerificationStatus.APPROVED


@pytest.mark.asyncio
async def test_non_allowlisted_adult_stays_pending(client, db_session, monkeypatch):
    enable(monkeypatch)
    linked = await profile(client)
    email = "outside@example.com"
    code = await otp_request(client, email)
    adult = await AdultAccountRepository(db_session).get_by_email(email)
    assert adult is not None
    own_profile = await link_existing(db_session, adult, linked["id"])
    assert (await otp_verify(client, email, code)).status_code == 200
    pending = await verification(db_session, adult.id, own_profile.id)
    assert pending is not None and pending.status == RepresentativeVerificationStatus.PENDING
    assert await approval_count(db_session, own_profile.id) == 0


@pytest.mark.asyncio
async def test_otp_does_not_change_approved_rejected_revoked_or_inactive(
    client, db_session, monkeypatch,
):
    enable(monkeypatch)
    profiles = [await profile(client) for _ in range(4)]
    code = await otp_request(client, EMAIL_ONE)
    adult = await AdultAccountRepository(db_session).get_by_email(EMAIL_ONE)
    assert adult is not None
    active_profiles = [await link_existing(db_session, adult, item["id"]) for item in profiles]
    service = RepresentativeVerificationService(db_session)
    approved = await service.approve(adult.id, active_profiles[0].id, verified_by="manual-mvp")
    rejected = await service.reject(adult.id, active_profiles[1].id, verified_by="manual-mvp")
    assert await AdultProfileService(db_session).revoke_link(adult, active_profiles[2].id)
    active_profiles[3].active = False
    await db_session.commit()

    assert (await otp_verify(client, EMAIL_ONE, code)).status_code == 200
    assert (await verification(db_session, adult.id, active_profiles[0].id)).id == approved.id
    assert (await verification(db_session, adult.id, active_profiles[0].id)).verified_by == "manual-mvp"
    assert (await verification(db_session, adult.id, active_profiles[1].id)).id == rejected.id
    assert (await verification(db_session, adult.id, active_profiles[1].id)).status == RepresentativeVerificationStatus.REJECTED
    for own_profile in active_profiles[2:]:
        assert (await verification(db_session, adult.id, own_profile.id)).status == RepresentativeVerificationStatus.PENDING
    for own_profile in active_profiles:
        assert await approval_count(db_session, own_profile.id) == (1 if own_profile.id == approved.profile_id else 0)


@pytest.mark.asyncio
async def test_enabled_without_two_valid_emails_approves_nobody(client, db_session, monkeypatch):
    enable(monkeypatch, "")
    linked = await profile(client)
    code = await otp_request(client, EMAIL_ONE)
    adult = await AdultAccountRepository(db_session).get_by_email(EMAIL_ONE)
    assert adult is not None
    own_profile = await link_existing(db_session, adult, linked["id"])
    assert (await otp_verify(client, EMAIL_ONE, code)).status_code == 200
    assert (await verification(db_session, adult.id, own_profile.id)).status == RepresentativeVerificationStatus.PENDING


@pytest.mark.parametrize("raw", [
    "", EMAIL_ONE, f"{EMAIL_ONE},{EMAIL_ONE.upper()}",
    f"{EMAIL_ONE},*@example.com", f"{EMAIL_ONE},bad-address",
    f"{EMAIL_ONE},{EMAIL_TWO},third@example.com",
])
def test_allowlist_rejects_invalid_or_non_distinct_pairs(raw):
    assert not auto_approval_allowed(True, raw, EMAIL_ONE)


def test_allowlist_requires_complete_email_and_enabled_flag():
    raw = f" {EMAIL_ONE.upper()} , {EMAIL_TWO} "
    assert auto_approval_allowed(True, raw, EMAIL_ONE)
    assert not auto_approval_allowed(False, raw, EMAIL_ONE)
    assert not auto_approval_allowed(True, raw, "pilot-one@example.com.evil")
    assert not auto_approval_allowed(True, raw, "someone@example.com")
