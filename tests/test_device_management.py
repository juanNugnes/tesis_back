import asyncio
from datetime import date
import uuid

import pytest
from sqlalchemy import func, select

from app.db.models import (
    AuditEvent,
    AuditEventType,
    DailyUsage,
    Device,
    Profile,
    ProfileLinkCode,
    UsageSnapshot,
)
from app.repositories.adult_account_repository import AdultAccountRepository
from app.services.representative_verification_service import (
    RepresentativeVerificationService,
)


async def create_managed_profile(client, db_session, *, email, approve=True):
    created = await client.post(
        "/api/v1/profiles",
        json={"alias": "Managed profile", "age": 9, "avatar": "fox"},
    )
    assert created.status_code == 201
    profile = created.json()
    registered = await client.post(
        f"/api/v1/profiles/{profile['id']}/devices",
        headers={"Authorization": f"Bearer {profile['bootstrap_token']}"},
        json={"installation_id": f"managed-{uuid.uuid4()}", "platform": "ANDROID"},
    )
    assert registered.status_code == 201
    device = registered.json()
    device_headers = {"Authorization": f"Bearer {device['access_token']}"}

    generated = await client.post(
        f"/api/v1/profiles/{profile['id']}/adult-link-codes",
        headers=device_headers,
    )
    assert generated.status_code == 201
    requested = await client.post("/api/v1/auth/adult/otp/request", json={"email": email})
    verified = await client.post(
        "/api/v1/auth/adult/otp/verify",
        json={"email": email, "code": requested.json()["dev_code"]},
    )
    adult_headers = {"Authorization": f"Bearer {verified.json()['access_token']}"}
    claimed = await client.post(
        "/api/v1/adult/profile-links/claim",
        headers=adult_headers,
        json={"code": generated.json()["code"]},
    )
    assert claimed.status_code == 200
    adult = await AdultAccountRepository(db_session).get_by_email(email)
    assert adult is not None
    if approve:
        await RepresentativeVerificationService(db_session).approve(
            adult.id,
            uuid.UUID(profile["id"]),
            verified_by="pytest",
        )
    return profile, device, device_headers, adult_headers, adult


def usage_payload(day="2026-10-17"):
    return {
        "snapshot_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"device-management:{day}")),
        "captured_at": f"{day}T12:00:00Z",
        "date": day,
        "period_start_at": f"{day}T00:00:00Z",
        "period_end_at": f"{day}T11:00:00Z",
        "timezone": "UTC",
        "duration_unit": "MINUTES",
        "measurement_method": "ANDROID_USAGE_STATS_AGGREGATES",
        "calculation_version": "1.0",
        "coverage_status": "UNKNOWN",
        "total_usage_minutes": 20,
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
    }


@pytest.mark.asyncio
async def test_verified_adult_lists_only_own_profile_devices(client, db_session):
    own, own_device, _, own_headers, _ = await create_managed_profile(
        client,
        db_session,
        email="device-list-owner@example.com",
    )
    foreign, _, _, _, _ = await create_managed_profile(
        client,
        db_session,
        email="device-list-foreign@example.com",
    )

    response = await client.get(
        f"/api/v1/profiles/{own['id']}/devices",
        headers=own_headers,
    )
    forbidden = await client.get(
        f"/api/v1/profiles/{foreign['id']}/devices",
        headers=own_headers,
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [own_device["id"]]
    assert response.json()[0]["active"] is True
    assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_unverified_adult_cannot_manage_devices(client, db_session):
    profile, _, _, adult_headers, _ = await create_managed_profile(
        client,
        db_session,
        email="device-list-pending@example.com",
        approve=False,
    )

    response = await client.get(
        f"/api/v1/profiles/{profile['id']}/devices",
        headers=adult_headers,
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Verified profile access required"}


@pytest.mark.asyncio
async def test_deactivate_device_is_idempotent_revokes_codes_and_preserves_usage(
    client,
    db_session,
    monitoring_authorization,
):
    profile, device, device_headers, adult_headers, adult = await create_managed_profile(
        client,
        db_session,
        email="device-deactivate@example.com",
    )
    await monitoring_authorization(profile["id"], adult=adult)
    usage = await client.post(
        f"/api/v1/profiles/{profile['id']}/usage",
        headers=device_headers,
        json=usage_payload(),
    )
    assert usage.status_code == 201
    pending_code = await client.post(
        f"/api/v1/profiles/{profile['id']}/adult-link-codes",
        headers=device_headers,
    )
    assert pending_code.status_code == 201

    url = f"/api/v1/profiles/{profile['id']}/devices/{device['id']}"
    first = await client.delete(url, headers=adult_headers)
    repeated = await client.delete(url, headers=adult_headers)

    assert first.status_code == repeated.status_code == 200
    assert first.json()["active"] is False
    assert repeated.json()["active"] is False
    listed = await client.get(
        f"/api/v1/profiles/{profile['id']}/devices",
        headers=adult_headers,
    )
    assert listed.json()[0]["active"] is False
    assert (
        await client.get(f"/api/v1/profiles/{profile['id']}", headers=device_headers)
    ).status_code == 401

    assert await db_session.scalar(
        select(func.count()).select_from(DailyUsage).where(
            DailyUsage.profile_id == uuid.UUID(profile["id"]),
            DailyUsage.date == date(2026, 10, 17),
        )
    ) == 1
    assert await db_session.scalar(
        select(func.count()).select_from(UsageSnapshot).where(
            UsageSnapshot.profile_id == uuid.UUID(profile["id"])
        )
    ) == 1
    stored_code = await db_session.scalar(
        select(ProfileLinkCode).where(
            ProfileLinkCode.profile_id == uuid.UUID(profile["id"]),
            ProfileLinkCode.code_hash.is_not(None),
            ProfileLinkCode.purpose == "ADULT_PROFILE_LINK",
            ProfileLinkCode.used_at.is_(None),
        )
    )
    assert stored_code is not None and stored_code.revoked_at is not None
    events = list(
        (
            await db_session.scalars(
                select(AuditEvent).where(
                    AuditEvent.profile_id == uuid.UUID(profile["id"]),
                    AuditEvent.event_type == AuditEventType.DEVICE_DEACTIVATED,
                )
            )
        ).all()
    )
    assert len(events) == 1
    assert events[0].actor_id == adult.id
    assert events[0].event_metadata == {
        "device_id": device["id"],
        "revoked_pending_adult_link_codes": 1,
    }


@pytest.mark.asyncio
async def test_cannot_deactivate_device_from_another_profile(client, db_session):
    own, own_device, _, own_headers, _ = await create_managed_profile(
        client,
        db_session,
        email="device-owner-a@example.com",
    )
    foreign, foreign_device, foreign_device_headers, _, _ = await create_managed_profile(
        client,
        db_session,
        email="device-owner-b@example.com",
    )
    pending_foreign_code = await client.post(
        f"/api/v1/profiles/{foreign['id']}/adult-link-codes",
        headers=foreign_device_headers,
    )
    assert pending_foreign_code.status_code == 201

    response = await client.delete(
        f"/api/v1/profiles/{own['id']}/devices/{foreign_device['id']}",
        headers=own_headers,
    )
    own_deactivation = await client.delete(
        f"/api/v1/profiles/{own['id']}/devices/{own_device['id']}",
        headers=own_headers,
    )

    assert response.status_code == 404
    assert own_deactivation.status_code == 200
    stored = await db_session.get(Device, uuid.UUID(foreign_device["id"]))
    assert stored is not None and stored.active is True
    foreign_code = await db_session.scalar(
        select(ProfileLinkCode).where(
            ProfileLinkCode.profile_id == uuid.UUID(foreign["id"]),
            ProfileLinkCode.purpose == "ADULT_PROFILE_LINK",
            ProfileLinkCode.used_at.is_(None),
        )
    )
    assert foreign_code is not None and foreign_code.revoked_at is None


@pytest.mark.asyncio
async def test_concurrent_device_deactivation_creates_one_audit_event(client, db_session):
    profile, device, _, adult_headers, _ = await create_managed_profile(
        client,
        db_session,
        email="device-concurrent@example.com",
    )
    url = f"/api/v1/profiles/{profile['id']}/devices/{device['id']}"

    responses = await asyncio.gather(
        client.delete(url, headers=adult_headers),
        client.delete(url, headers=adult_headers),
    )

    assert [response.status_code for response in responses] == [200, 200]
    assert all(response.json()["active"] is False for response in responses)
    assert await db_session.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.profile_id == uuid.UUID(profile["id"]),
            AuditEvent.event_type == AuditEventType.DEVICE_DEACTIVATED,
        )
    ) == 1


@pytest.mark.asyncio
async def test_profile_update_rejects_active_but_keeps_legitimate_edits(client, db_session):
    profile, _, device_headers, adult_headers, _ = await create_managed_profile(
        client,
        db_session,
        email="profile-active-edit@example.com",
    )
    url = f"/api/v1/profiles/{profile['id']}"

    adult_attempt = await client.put(url, headers=adult_headers, json={"active": False})
    device_attempt = await client.put(url, headers=device_headers, json={"active": False})
    legitimate = await client.put(url, headers=adult_headers, json={"alias": "Updated alias"})

    assert adult_attempt.status_code == device_attempt.status_code == 422
    assert legitimate.status_code == 200
    assert legitimate.json()["alias"] == "Updated alias"
    stored = await db_session.get(Profile, uuid.UUID(profile["id"]))
    assert stored is not None and stored.active is True
