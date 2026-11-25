import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.db.models import AdultAccount, AdultWeeklyDeclaration
from app.repositories.adult_message_window_repository import (
    AdultMessageWindowRepository,
)
from app.repositories.adult_weekly_declaration_repository import (
    AdultWeeklyDeclarationRepository,
)
from app.schemas.adult_weekly_declaration import AdultWeeklyDeclarationCreate
from app.services.adult_message_window_service import AdultMessageWindowService
from app.services.adult_weekly_declaration_service import (
    AdultWeeklyDeclarationDateError,
    AdultWeeklyDeclarationService,
)
from app.services.representative_verification_service import (
    RepresentativeVerificationService,
)
from test_adult_message_window_service import (
    EVALUATION_DATE,
    WINDOW_START,
    add_evidence,
    create_profile,
)
from test_f12_evidence import create_profile_and_device


def declaration_data(
    device_id,
    *,
    effective_from=WINDOW_START,
    effective_to=EVALUATION_DATE - timedelta(days=1),
    assignment_verified=True,
    shared_phone=False,
    context_declared=False,
    time_zone="UTC",
):
    return AdultWeeklyDeclarationCreate(
        device_id=device_id,
        time_zone=time_zone,
        effective_from=effective_from,
        effective_to=effective_to,
        assignment_verified=assignment_verified,
        shared_phone=shared_phone,
        context_declared=context_declared,
    )


async def create_declaration(
    db,
    profile,
    device,
    adult,
    *,
    data=None,
    now=None,
):
    return await AdultWeeklyDeclarationService(
        AdultWeeklyDeclarationRepository(db)
    ).create(
        profile_id=profile.id,
        adult=adult,
        data=data or declaration_data(device.id),
        now=now or datetime.combine(WINDOW_START, datetime.min.time(), tzinfo=UTC),
    )


async def diagnostic(db, profile_id):
    return await AdultMessageWindowService(
        AdultMessageWindowRepository(db)
    ).diagnose(profile_id=profile_id, evaluation_date=EVALUATION_DATE)


@pytest.mark.asyncio
async def test_complete_declaration_history_resolves_three_contract_conditions(
    db_session,
):
    profile, device = await create_profile(db_session)
    adult = AdultAccount(email=f"declaration-{uuid.uuid4()}@example.com")
    db_session.add(adult)
    await db_session.flush()
    await add_evidence(db_session, profile, device, WINDOW_START)
    declaration = await create_declaration(db_session, profile, device, adult)

    result = await diagnostic(db_session, profile.id)

    assert declaration.revision == 1
    assert result.declarations.assignment.complete_for_window is True
    assert result.declarations.assignment.contract_value is True
    assert result.declarations.assignment.problem is None
    assert result.declarations.shared_phone.complete_for_window is True
    assert result.declarations.shared_phone.contract_value is False
    assert result.declarations.shared_phone.problem is None
    assert result.declarations.family_context.complete_for_window is True
    assert result.declarations.family_context.contract_value is False
    assert result.declarations.family_context.problem is None
    declaration_codes = {
        "ASSIGNMENT_HISTORY_MISSING",
        "SHARED_PHONE_HISTORY_MISSING",
        "CONTEXT_HISTORY_MISSING",
    }
    assert not declaration_codes.intersection(item.code for item in result.blockers)
    assert "F12_QUALITY_NOT_VERIFIED" in {item.code for item in result.blockers}
    assert result.evaluation_ready is False


@pytest.mark.asyncio
async def test_partial_declaration_reports_exact_uncovered_dates(db_session):
    profile, device = await create_profile(db_session)
    adult = AdultAccount(email=f"partial-{uuid.uuid4()}@example.com")
    db_session.add(adult)
    await db_session.flush()
    await add_evidence(db_session, profile, device, WINDOW_START)
    start = WINDOW_START + timedelta(days=5)
    await create_declaration(
        db_session,
        profile,
        device,
        adult,
        data=declaration_data(device.id, effective_from=start),
        now=datetime.combine(start, datetime.min.time(), tzinfo=UTC),
    )

    result = await diagnostic(db_session, profile.id)

    expected_missing = [WINDOW_START + timedelta(days=index) for index in range(5)]
    assert result.declarations.assignment.missing_dates == expected_missing
    assert result.declarations.shared_phone.missing_dates == expected_missing
    assert result.declarations.family_context.missing_dates == expected_missing
    assert result.declarations.assignment.problem == "ASSIGNMENT_HISTORY_MISSING"
    assert result.evaluation_ready is False


@pytest.mark.asyncio
async def test_new_revision_changes_only_from_its_effective_date(db_session):
    profile, device = await create_profile(db_session)
    adult = AdultAccount(email=f"revision-{uuid.uuid4()}@example.com")
    db_session.add(adult)
    await db_session.flush()
    await add_evidence(db_session, profile, device, WINDOW_START)
    first = await create_declaration(db_session, profile, device, adult)
    change_date = WINDOW_START + timedelta(days=15)
    second = await create_declaration(
        db_session,
        profile,
        device,
        adult,
        data=declaration_data(
            device.id,
            effective_from=change_date,
            context_declared=True,
        ),
        now=datetime.combine(change_date, datetime.min.time(), tzinfo=UTC),
    )

    result = await diagnostic(db_session, profile.id)

    assert (first.revision, second.revision) == (1, 2)
    assert result.declarations.assignment.contract_value is True
    assert result.declarations.assignment.problem is None
    assert result.declarations.shared_phone.contract_value is False
    assert result.declarations.shared_phone.problem is None
    assert result.declarations.family_context.contract_value is None
    assert (
        result.declarations.family_context.problem
        == "CONTEXT_CHANGED_WITHIN_WINDOW"
    )
    assert result.declarations.family_context.revisions == [1, 2]


@pytest.mark.asyncio
async def test_revocation_preserves_prior_days_and_removes_future_coverage(db_session):
    profile, device = await create_profile(db_session)
    adult = AdultAccount(email=f"revocation-{uuid.uuid4()}@example.com")
    db_session.add(adult)
    await db_session.flush()
    await add_evidence(db_session, profile, device, WINDOW_START)
    declaration = await create_declaration(db_session, profile, device, adult)
    revoke_date = WINDOW_START + timedelta(days=12)
    await AdultWeeklyDeclarationService(
        AdultWeeklyDeclarationRepository(db_session)
    ).revoke(
        profile_id=profile.id,
        declaration_id=declaration.id,
        adult=adult,
        now=datetime.combine(revoke_date, datetime.min.time(), tzinfo=UTC),
    )

    result = await diagnostic(db_session, profile.id)

    assert result.declarations.assignment.covered_dates[-1] == revoke_date - timedelta(days=1)
    assert result.declarations.assignment.missing_dates[0] == revoke_date
    assert result.declarations.assignment.problem == "ASSIGNMENT_HISTORY_MISSING"
    assert declaration.revoked_effective_date == revoke_date
    assert declaration.revoked_by_adult_id == adult.id


@pytest.mark.asyncio
async def test_service_rejects_retroactive_declaration(db_session):
    profile, device = await create_profile(db_session)
    adult = AdultAccount(email=f"retroactive-{uuid.uuid4()}@example.com")
    db_session.add(adult)
    await db_session.flush()
    today = WINDOW_START + timedelta(days=1)

    with pytest.raises(AdultWeeklyDeclarationDateError):
        await create_declaration(
            db_session,
            profile,
            device,
            adult,
            data=declaration_data(device.id, effective_from=WINDOW_START),
            now=datetime.combine(today, datetime.min.time(), tzinfo=UTC),
        )

    assert not (await AdultWeeklyDeclarationRepository(db_session).list_for_profile(profile.id))


async def authorized_http_profile(client, adult_session, monitoring_authorization):
    profile, device, _device_headers = await create_profile_and_device(
        client, f"Weekly declarations {uuid.uuid4()}"
    )
    adult_headers, adult = await adult_session(profile["id"])
    await monitoring_authorization(profile["id"], adult=adult)
    return profile, device, adult_headers, adult


def endpoint(profile_id):
    return f"/api/v1/profiles/{profile_id}/weekly-declarations"


@pytest.mark.asyncio
async def test_adult_can_create_list_revise_and_revoke_via_minimal_api(
    client,
    adult_session,
    monitoring_authorization,
):
    profile, device, headers, _adult = await authorized_http_profile(
        client, adult_session, monitoring_authorization
    )
    today = datetime.now(UTC).date()
    first_payload = {
        "device_id": device["id"],
        "time_zone": "UTC",
        "effective_from": today.isoformat(),
        "effective_to": None,
        "assignment_verified": True,
        "shared_phone": False,
        "context_declared": False,
    }
    first = await client.post(endpoint(profile["id"]), headers=headers, json=first_payload)
    assert first.status_code == 201, first.text
    assert first.headers["cache-control"] == "no-store"
    assert first.json()["revision"] == 1

    second_payload = {
        **first_payload,
        "effective_from": (today + timedelta(days=1)).isoformat(),
        "shared_phone": True,
    }
    second = await client.post(endpoint(profile["id"]), headers=headers, json=second_payload)
    assert second.status_code == 201, second.text
    assert second.json()["revision"] == 2

    listed = await client.get(endpoint(profile["id"]), headers=headers)
    assert listed.status_code == 200
    assert [item["revision"] for item in listed.json()] == [2, 1]

    revoked = await client.delete(
        f"{endpoint(profile['id'])}/{second.json()['id']}", headers=headers
    )
    assert revoked.status_code == 200
    assert revoked.json()["revoked_effective_date"] == today.isoformat()
    repeated = await client.delete(
        f"{endpoint(profile['id'])}/{second.json()['id']}", headers=headers
    )
    assert repeated.status_code == 200
    assert repeated.json()["revoked_at"] == revoked.json()["revoked_at"]


@pytest.mark.asyncio
async def test_api_rejects_retroactive_wrong_device_and_unauthorized_access(
    client,
    db_session,
    adult_session,
    monitoring_authorization,
):
    profile, device, headers, _adult = await authorized_http_profile(
        client, adult_session, monitoring_authorization
    )
    today = datetime.now(UTC).date()
    base = {
        "device_id": device["id"],
        "time_zone": "UTC",
        "effective_from": today.isoformat(),
        "effective_to": None,
        "assignment_verified": True,
        "shared_phone": False,
        "context_declared": False,
    }
    retroactive = await client.post(
        endpoint(profile["id"]),
        headers=headers,
        json={**base, "effective_from": (today - timedelta(days=1)).isoformat()},
    )
    assert retroactive.status_code == 422
    wrong_device = await client.post(
        endpoint(profile["id"]),
        headers=headers,
        json={**base, "device_id": str(uuid.uuid4())},
    )
    assert wrong_device.status_code == 422

    other_profile, _other_device, other_headers, _ = await authorized_http_profile(
        client, adult_session, monitoring_authorization
    )
    denied = await client.get(endpoint(profile["id"]), headers=other_headers)
    assert denied.status_code == 403

    unverified_profile, _ = await create_profile(db_session, alias="Unverified weekly")
    await db_session.commit()
    unverified_headers, _ = await adult_session(unverified_profile.id)
    unverified = await client.get(
        endpoint(unverified_profile.id), headers=unverified_headers
    )
    assert unverified.status_code == 403


@pytest.mark.asyncio
async def test_verified_adult_without_monitoring_consent_is_rejected(
    client,
    db_session,
    adult_session,
):
    profile, _device, _device_headers = await create_profile_and_device(
        client, "Weekly without consent"
    )
    headers, adult = await adult_session(profile["id"])
    await RepresentativeVerificationService(db_session).approve(
        adult.id,
        uuid.UUID(profile["id"]),
        verified_by="pytest",
    )

    response = await client.get(endpoint(profile["id"]), headers=headers)

    assert response.status_code == 403
    assert response.json()["detail"] == "Monitoring authorization is not active"


@pytest.mark.asyncio
async def test_creation_uses_buenos_aires_local_date_at_utc_day_boundary(db_session):
    profile, device = await create_profile(db_session)
    adult = AdultAccount(email=f"local-date-{uuid.uuid4()}@example.com")
    db_session.add(adult)
    await db_session.flush()
    service = AdultWeeklyDeclarationService(
        AdultWeeklyDeclarationRepository(db_session)
    )
    instant = datetime(2026, 11, 26, 0, 30, tzinfo=UTC)

    declaration = await service.create(
        profile_id=profile.id,
        adult=adult,
        data=declaration_data(
            device.id,
            effective_from=datetime(2026, 11, 25).date(),
            effective_to=None,
            time_zone="America/Argentina/Buenos_Aires",
        ),
        now=instant,
    )

    assert declaration.effective_from.isoformat() == "2026-11-25"
    assert declaration.time_zone == "America/Argentina/Buenos_Aires"
    assert declaration.created_at.replace(tzinfo=UTC) == instant


@pytest.mark.asyncio
async def test_local_midnight_rejects_previous_day_and_accepts_current_day(db_session):
    profile, device = await create_profile(db_session)
    adult = AdultAccount(email=f"midnight-{uuid.uuid4()}@example.com")
    db_session.add(adult)
    await db_session.flush()
    service = AdultWeeklyDeclarationService(
        AdultWeeklyDeclarationRepository(db_session)
    )
    after_midnight = datetime(2026, 11, 26, 3, 1, tzinfo=UTC)

    with pytest.raises(AdultWeeklyDeclarationDateError):
        await service.create(
            profile_id=profile.id,
            adult=adult,
            data=declaration_data(
                device.id,
                effective_from=datetime(2026, 11, 25).date(),
                effective_to=None,
                time_zone="America/Argentina/Buenos_Aires",
            ),
            now=after_midnight,
        )
    accepted = await service.create(
        profile_id=profile.id,
        adult=adult,
        data=declaration_data(
            device.id,
            effective_from=datetime(2026, 11, 26).date(),
            effective_to=None,
            time_zone="America/Argentina/Buenos_Aires",
        ),
        now=after_midnight,
    )
    assert accepted.effective_from.isoformat() == "2026-11-26"


@pytest.mark.asyncio
async def test_revocation_uses_declaration_zone_and_preserves_utc_timestamp(db_session):
    profile, device = await create_profile(db_session)
    adult = AdultAccount(email=f"local-revoke-{uuid.uuid4()}@example.com")
    db_session.add(adult)
    await db_session.flush()
    service = AdultWeeklyDeclarationService(
        AdultWeeklyDeclarationRepository(db_session)
    )
    declaration = await service.create(
        profile_id=profile.id,
        adult=adult,
        data=declaration_data(
            device.id,
            effective_from=datetime(2026, 11, 25).date(),
            effective_to=None,
            time_zone="America/Argentina/Buenos_Aires",
        ),
        now=datetime(2026, 11, 25, 15, 0, tzinfo=UTC),
    )
    revoked_at = datetime(2026, 11, 26, 2, 30, tzinfo=UTC)

    await service.revoke(
        profile_id=profile.id,
        declaration_id=declaration.id,
        adult=adult,
        now=revoked_at,
    )

    assert declaration.revoked_effective_date.isoformat() == "2026-11-25"
    assert declaration.revoked_at.replace(tzinfo=UTC) == revoked_at


@pytest.mark.asyncio
async def test_creation_uses_declared_non_argentina_time_zone(db_session):
    profile, device = await create_profile(db_session)
    adult = AdultAccount(email=f"tokyo-{uuid.uuid4()}@example.com")
    db_session.add(adult)
    await db_session.flush()
    instant = datetime(2026, 11, 25, 15, 30, tzinfo=UTC)

    declaration = await AdultWeeklyDeclarationService(
        AdultWeeklyDeclarationRepository(db_session)
    ).create(
        profile_id=profile.id,
        adult=adult,
        data=declaration_data(
            device.id,
            effective_from=datetime(2026, 11, 26).date(),
            effective_to=None,
            time_zone="Asia/Tokyo",
        ),
        now=instant,
    )

    assert declaration.effective_from.isoformat() == "2026-11-26"
    assert declaration.time_zone == "Asia/Tokyo"
