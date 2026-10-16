from datetime import date, datetime, timezone
import uuid

import pytest
from sqlalchemy import func, select

from app.db.models import (
    AdultProfileLink,
    AuditActorType,
    AuditEvent,
    AuditEventType,
    Consent,
    ConsentStatus,
    DailyUsage,
    Profile,
    RepresentativeVerificationStatus,
)
from app.services.representative_verification_service import RepresentativeVerificationService


async def create_profile_and_device(client, alias="Consent profile"):
    created = await client.post(
        "/api/v1/profiles",
        json={"alias": alias, "age": 9, "avatar": "fox"},
    )
    assert created.status_code == 201
    profile = created.json()
    registered = await client.post(
        f"/api/v1/profiles/{profile['id']}/devices",
        headers={"Authorization": f"Bearer {profile['bootstrap_token']}"},
        json={"installation_id": f"consent-{uuid.uuid4()}", "platform": "ANDROID"},
    )
    assert registered.status_code == 201
    return profile, {"Authorization": f"Bearer {registered.json()['access_token']}"}


def grant_payload(version="1.0"):
    return {
        "privacy_notice_version": version,
        "purposes": ["USAGE_MONITORING"],
    }


def usage_payload(day="2026-10-05"):
    return {
        "snapshot_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"consent:{day}")),
        "captured_at": f"{day}T12:00:00Z",
        "date": day,
        "period_start_at": f"{day}T00:00:00Z",
        "period_end_at": f"{day}T11:00:00Z",
        "timezone": "UTC",
        "duration_unit": "MINUTES",
        "measurement_method": "ANDROID_USAGE_STATS_AGGREGATES",
        "calculation_version": "1.0",
        "coverage_status": "UNKNOWN",
        "total_usage_minutes": 12,
        "night_usage_minutes": 0,
        "metric_statuses": {
            "total_usage_minutes": "MEASURED",
            "session_count": "UNAVAILABLE",
            "night_usage_minutes": "MEASURED",
            "max_session_minutes": "UNAVAILABLE",
            "unlock_count": "UNAVAILABLE",
            "applications": "MEASURED",
        },
        "applications": [],
        "night_window": {
            "source": "PROFILE_SLEEP_SCHEDULE",
            "start_local_time": "22:00:00",
            "end_local_time": "07:00:00",
        },
    }


async def approve(db_session, adult, profile_id, *, verified_by="manual-mvp"):
    return await RepresentativeVerificationService(db_session).approve(
        adult.id,
        uuid.UUID(profile_id),
        verified_by=verified_by,
    )


@pytest.mark.asyncio
async def test_verification_starts_pending_is_scoped_and_has_no_public_approval(
    client,
    db_session,
    adult_session,
):
    profile_a, _ = await create_profile_and_device(client, "A")
    profile_b, _ = await create_profile_and_device(client, "B")
    headers_a, adult_a = await adult_session(profile_a["id"], email="scope-a@example.com")
    headers_b, _ = await adult_session(profile_b["id"], email="scope-b@example.com")

    current = await client.get(
        f"/api/v1/profiles/{profile_a['id']}/representative-verification",
        headers=headers_a,
    )
    foreign = await client.get(
        f"/api/v1/profiles/{profile_a['id']}/representative-verification",
        headers=headers_b,
    )
    public_approval = await client.post(
        f"/api/v1/profiles/{profile_a['id']}/representative-verification/approve",
        headers=headers_a,
    )

    assert current.status_code == 200
    assert current.json()["status"] == "PENDING"
    assert foreign.status_code == 403
    assert public_approval.status_code == 404
    verification = await approve(db_session, adult_a, profile_a["id"])
    assert verification.status == RepresentativeVerificationStatus.APPROVED


@pytest.mark.asyncio
async def test_pending_and_rejected_verification_block_consent_but_approved_allows_it(
    client,
    db_session,
    adult_session,
):
    profile, _ = await create_profile_and_device(client)
    headers, adult = await adult_session(profile["id"], email="decisions@example.com")
    url = f"/api/v1/profiles/{profile['id']}/consents"

    pending = await client.post(url, headers=headers, json=grant_payload())
    await RepresentativeVerificationService(db_session).reject(
        adult.id,
        uuid.UUID(profile["id"]),
        verified_by="manual-mvp",
        notes="Insufficient evidence",
    )
    rejected = await client.post(url, headers=headers, json=grant_payload())
    await approve(db_session, adult, profile["id"])
    granted = await client.post(url, headers=headers, json=grant_payload())

    assert pending.status_code == rejected.status_code == 403
    assert granted.status_code == 201
    assert granted.json()["status"] == "ACTIVE"
    assert granted.json()["purposes"] == ["USAGE_MONITORING"]


@pytest.mark.asyncio
async def test_consent_requires_link_active_profile_and_valid_request(
    client,
    db_session,
    adult_session,
):
    profile, _ = await create_profile_and_device(client)
    headers, adult = await adult_session(profile["id"], email="validation@example.com")
    await approve(db_session, adult, profile["id"])
    url = f"/api/v1/profiles/{profile['id']}/consents"

    empty_version = await client.post(
        url,
        headers=headers,
        json={"privacy_notice_version": "   ", "purposes": ["USAGE_MONITORING"]},
    )
    invalid_purpose = await client.post(
        url,
        headers=headers,
        json={"privacy_notice_version": "1.0", "purposes": ["ANALYSIS"]},
    )
    no_link_headers, _ = await adult_session(
        (await create_profile_and_device(client, "Other"))[0]["id"],
        email="no-link@example.com",
    )
    no_link = await client.post(url, headers=no_link_headers, json=grant_payload())
    stored_profile = await db_session.get(Profile, uuid.UUID(profile["id"]))
    assert stored_profile is not None
    stored_profile.active = False
    await db_session.commit()
    inactive = await client.post(url, headers=headers, json=grant_payload())

    assert empty_version.status_code == invalid_purpose.status_code == 422
    assert no_link.status_code == inactive.status_code == 403


@pytest.mark.asyncio
async def test_grant_is_idempotent_and_new_grant_after_revoke_preserves_history(
    client,
    db_session,
    adult_session,
):
    profile, _ = await create_profile_and_device(client)
    headers, adult = await adult_session(profile["id"], email="history@example.com")
    await approve(db_session, adult, profile["id"])
    url = f"/api/v1/profiles/{profile['id']}/consents"

    first = await client.post(url, headers=headers, json=grant_payload())
    duplicate = await client.post(url, headers=headers, json=grant_payload())
    revoked = await client.post(
        f"{url}/{first.json()['id']}/revoke",
        headers=headers,
    )
    repeated_revoke = await client.post(
        f"{url}/{first.json()['id']}/revoke",
        headers=headers,
    )
    second = await client.post(url, headers=headers, json=grant_payload("1.1"))
    current = await client.get(f"{url}/current", headers=headers)

    assert first.status_code == second.status_code == 201
    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == first.json()["id"]
    assert revoked.status_code == repeated_revoke.status_code == 200
    assert revoked.json()["status"] == "REVOKED"
    assert second.json()["id"] != first.json()["id"]
    assert current.json()["id"] == second.json()["id"]
    assert await db_session.scalar(select(func.count()).select_from(Consent)) == 2
    active_count = await db_session.scalar(
        select(func.count()).select_from(Consent).where(Consent.status == ConsentStatus.ACTIVE)
    )
    revoke_events = await db_session.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.event_type == AuditEventType.CONSENT_REVOKED)
    )
    assert active_count == 1
    assert revoke_events == 1


@pytest.mark.asyncio
async def test_same_device_jwt_is_blocked_immediately_after_revoke_and_old_payload_is_not_saved(
    client,
    db_session,
    adult_session,
):
    profile, device_headers = await create_profile_and_device(client)
    adult_headers, adult = await adult_session(profile["id"], email="revoke@example.com")
    await approve(db_session, adult, profile["id"])
    granted = await client.post(
        f"/api/v1/profiles/{profile['id']}/consents",
        headers=adult_headers,
        json=grant_payload(),
    )

    before = await client.post(
        f"/api/v1/profiles/{profile['id']}/usage",
        headers=device_headers,
        json=usage_payload("2026-10-05"),
    )
    await client.post(
        f"/api/v1/profiles/{profile['id']}/consents/{granted.json()['id']}/revoke",
        headers=adult_headers,
    )
    after = await client.post(
        f"/api/v1/profiles/{profile['id']}/usage",
        headers=device_headers,
        json=usage_payload("2020-01-01"),
    )

    assert before.status_code == 201
    assert after.status_code == 403
    assert after.json() == {"detail": "Monitoring authorization is not active"}
    assert await db_session.scalar(
        select(func.count()).select_from(DailyUsage).where(DailyUsage.date == date(2020, 1, 1))
    ) == 0


@pytest.mark.asyncio
async def test_authorization_does_not_cross_profiles_and_one_valid_adult_is_sufficient(
    client,
    db_session,
    adult_session,
    monitoring_authorization,
):
    profile_a, device_a = await create_profile_and_device(client, "Authorized")
    profile_b, device_b = await create_profile_and_device(client, "Unauthorized")
    _, adult_a = await adult_session(profile_a["id"], email="valid@example.com")
    await adult_session(profile_a["id"], email="pending@example.com")
    await monitoring_authorization(profile_a["id"], adult=adult_a)

    allowed = await client.post(
        f"/api/v1/profiles/{profile_a['id']}/usage",
        headers=device_a,
        json=usage_payload(),
    )
    blocked = await client.post(
        f"/api/v1/profiles/{profile_b['id']}/usage",
        headers=device_b,
        json=usage_payload(),
    )

    assert allowed.status_code == 201
    assert blocked.status_code == 403


@pytest.mark.asyncio
async def test_revoked_link_and_later_rejected_verification_each_stop_usage(
    client,
    db_session,
    adult_session,
    monitoring_authorization,
):
    profile, device_headers = await create_profile_and_device(client)
    _, adult = await adult_session(profile["id"], email="state-change@example.com")
    consent = await monitoring_authorization(profile["id"], adult=adult)
    usage_url = f"/api/v1/profiles/{profile['id']}/usage"

    assert (
        await client.post(usage_url, headers=device_headers, json=usage_payload("2026-10-01"))
    ).status_code == 201
    link = await db_session.scalar(
        select(AdultProfileLink).where(
            AdultProfileLink.adult_id == adult.id,
            AdultProfileLink.profile_id == uuid.UUID(profile["id"]),
        )
    )
    assert link is not None
    link.active = False
    link.revoked_at = datetime.now(timezone.utc)
    await db_session.commit()
    assert (
        await client.post(usage_url, headers=device_headers, json=usage_payload("2026-10-02"))
    ).status_code == 403
    assert consent.status == ConsentStatus.ACTIVE

    link.active = True
    link.revoked_at = None
    await db_session.commit()
    await RepresentativeVerificationService(db_session).reject(
        adult.id,
        uuid.UUID(profile["id"]),
        verified_by="manual-mvp",
    )
    assert (
        await client.post(usage_url, headers=device_headers, json=usage_payload("2026-10-03"))
    ).status_code == 403


@pytest.mark.asyncio
async def test_audit_events_are_minimal_and_cover_required_transitions(
    client,
    db_session,
    adult_session,
):
    profile, _ = await create_profile_and_device(client)
    headers, adult = await adult_session(profile["id"], email="audit@example.com")
    service = RepresentativeVerificationService(db_session)
    await service.approve(
        adult.id,
        uuid.UUID(profile["id"]),
        verified_by="operator-7",
    )
    granted = await client.post(
        f"/api/v1/profiles/{profile['id']}/consents",
        headers=headers,
        json=grant_payload(),
    )
    await client.post(
        f"/api/v1/profiles/{profile['id']}/consents/{granted.json()['id']}/revoke",
        headers=headers,
    )
    await service.reject(
        adult.id,
        uuid.UUID(profile["id"]),
        verified_by="operator-7",
    )

    events = list(
        (
            await db_session.execute(
                select(AuditEvent)
                .where(AuditEvent.profile_id == uuid.UUID(profile["id"]))
                .order_by(AuditEvent.occurred_at)
            )
        ).scalars()
    )
    assert [event.event_type for event in events] == [
        AuditEventType.REPRESENTATIVE_VERIFICATION_APPROVED,
        AuditEventType.CONSENT_GRANTED,
        AuditEventType.CONSENT_REVOKED,
        AuditEventType.REPRESENTATIVE_VERIFICATION_REJECTED,
    ]
    assert events[0].actor_type == events[-1].actor_type == AuditActorType.INTERNAL
    assert events[1].actor_type == events[2].actor_type == AuditActorType.ADULT
    assert events[0].actor_id is None and events[-1].actor_id is None
    assert events[1].actor_id == events[2].actor_id == adult.id
    assert all(event.result.value == "SUCCESS" for event in events)
    serialized_metadata = str([event.event_metadata for event in events]).lower()
    for forbidden in ("otp", "jwt", "code", "total_usage_minutes", "applications"):
        assert forbidden not in serialized_metadata


def test_consent_openapi_contract_is_adult_only_and_does_not_accept_adult_id():
    from app.main import app

    schema = app.openapi()
    paths = schema["paths"]
    protected = (
        paths["/api/v1/profiles/{profile_id}/representative-verification"]["get"],
        paths["/api/v1/profiles/{profile_id}/consents"]["post"],
        paths["/api/v1/profiles/{profile_id}/consents/current"]["get"],
        paths["/api/v1/profiles/{profile_id}/consents/{consent_id}/revoke"]["post"],
    )
    assert all(operation["security"] == [{"AdultBearer": []}] for operation in protected)
    request_schema = schema["components"]["schemas"]["ConsentGrantRequest"]
    assert set(request_schema["properties"]) == {"privacy_notice_version", "purposes"}
    assert request_schema["additionalProperties"] is False
