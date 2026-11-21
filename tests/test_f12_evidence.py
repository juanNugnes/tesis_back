import hashlib
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import selectinload

from app.api.routes import profile_f12_evidence
from app.db.models import F12RawEvidenceCapture
from app.repositories.f12_evidence_repository import F12EvidenceRepository
from app.schemas.f12_evidence import F12_EXPORT_ADAPTER_VERSION
from app.services.f12_evidence_validator import unavailable_content_sha256


async def create_profile_and_device(client, alias):
    created = await client.post(
        "/api/v1/profiles",
        json={"alias": alias, "age": 9, "avatar": "fox"},
    )
    assert created.status_code == 201
    profile = created.json()
    registered = await client.post(
        f"/api/v1/profiles/{profile['id']}/devices",
        headers={"Authorization": f"Bearer {profile['bootstrap_token']}"},
        json={
            "installation_id": f"f12-evidence-{uuid.uuid4()}",
            "platform": "ANDROID",
        },
    )
    assert registered.status_code == 201
    device = registered.json()
    return profile, device, {
        "Authorization": f"Bearer {device['access_token']}"
    }


async def authorized_device(
    client,
    adult_session,
    monitoring_authorization,
    alias="F12 evidence",
):
    profile, device, headers = await create_profile_and_device(client, alias)
    _, adult = await adult_session(profile["id"])
    await monitoring_authorization(profile["id"], adult=adult)
    return profile, device, headers


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def capture_id(profile_id, device_id, day, revision, content_sha256):
    value = "\0".join(
        (str(profile_id), str(device_id), day.isoformat(), str(revision), content_sha256)
    ).encode()
    return sha256(value)


def available_upload(profile_id, device_id, *, revision=1, day=date(2026, 11, 18)):
    begin = int(datetime.combine(day, datetime.min.time(), tzinfo=UTC).timestamp() * 1000)
    end = begin + 24 * 60 * 60 * 1000
    collected = end + 60_000
    run_id = str(uuid.uuid4())
    summary = {
        "collectorRunId": run_id,
        "requestedBeginEpochMs": begin,
        "requestedEndEpochMs": end,
        "collectedAtEpochMs": collected,
        "usageAccessAvailable": True,
        "queryReturnedNull": False,
        "eventCount": 1,
        "errorCode": None,
        "errorMessage": None,
    }
    event = {
        "schemaVersion": "raw-usage-event-v1",
        "collectorRunId": run_id,
        "queryBeginEpochMs": begin,
        "queryEndEpochMs": end,
        "collectedAtEpochMs": collected,
        "eventTimeEpochMs": begin + 60_000,
        "eventTypeCode": 15,
        "eventTypeName": "SCREEN_INTERACTIVE",
        "packageName": None,
        "className": None,
        "androidApiLevel": 35,
        "manufacturer": "SYNTHETIC",
        "model": "NOT-A-PHONE",
        "queryOrdinal": 0,
    }
    summary_bytes = json.dumps(
        summary,
        ensure_ascii=False,
        indent=2,
    ).encode()
    events_bytes = (json.dumps(event, separators=(",", ":")) + "\n").encode()
    digest = hashlib.sha256()
    digest.update(summary_bytes)
    digest.update(b"\0")
    digest.update(events_bytes)
    digest.update(b"\0")
    content_hash = digest.hexdigest()
    manifest = {
        "envelope_version": 1,
        "capture_id": capture_id(
            profile_id,
            device_id,
            day,
            revision,
            content_hash,
        ),
        "adapter_version": F12_EXPORT_ADAPTER_VERSION,
        "local_date": day.isoformat(),
        "time_zone": "UTC",
        "revision": revision,
        "created_at_epoch_millis": collected + 60_000,
        "content_sha256": content_hash,
        "evidence_state": "AVAILABLE",
        "unavailable_reason": None,
        "pairs": [
            {
                "collector_run_id": run_id,
                "summary_sha256": sha256(summary_bytes),
                "events_sha256": sha256(events_bytes),
            }
        ],
    }
    return manifest, summary_bytes, events_bytes


def unavailable_upload(profile_id, device_id, *, revision=1, day=date(2026, 11, 18)):
    content_hash = unavailable_content_sha256()
    return {
        "envelope_version": 1,
        "capture_id": capture_id(
            profile_id,
            device_id,
            day,
            revision,
            content_hash,
        ),
        "adapter_version": F12_EXPORT_ADAPTER_VERSION,
        "local_date": day.isoformat(),
        "time_zone": "UTC",
        "revision": revision,
        "created_at_epoch_millis": int(
            datetime(2026, 11, 19, 0, 2, tzinfo=UTC).timestamp() * 1000
        ),
        "content_sha256": content_hash,
        "evidence_state": "UNAVAILABLE",
        "unavailable_reason": "NOT_EXPORTED",
        "pairs": [],
    }


def multipart(manifest, summary_bytes=None, events_bytes=None):
    parts = [
        (
            "manifest",
            (None, json.dumps(manifest, separators=(",", ":")), "application/json"),
        )
    ]
    if summary_bytes is not None:
        parts.append(("summary", ("summary.json", summary_bytes, "application/json")))
    if events_bytes is not None:
        parts.append(
            ("events", ("events.jsonl", events_bytes, "application/x-ndjson"))
        )
    return parts


def endpoint(profile_id):
    return f"/api/v1/profiles/{profile_id}/f12-evidence/v1"


@pytest.mark.asyncio
async def test_available_evidence_is_persisted_as_exact_original_bytes(
    client,
    db_session,
    adult_session,
    monitoring_authorization,
):
    profile, device, headers = await authorized_device(
        client, adult_session, monitoring_authorization
    )
    manifest, summary_bytes, events_bytes = available_upload(
        profile["id"], device["id"]
    )

    response = await client.post(
        endpoint(profile["id"]),
        headers=headers,
        files=multipart(manifest, summary_bytes, events_bytes),
    )

    assert response.status_code == 201, response.text
    assert response.headers["x-f12-evidence-write-result"] == "created"
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "receipt_id": response.json()["receipt_id"],
        "capture_id": manifest["capture_id"],
        "status": "RECEIVED",
        "evidence_state": "AVAILABLE",
        "local_date": "2026-11-18",
        "revision": 1,
        "content_sha256": manifest["content_sha256"],
        "pair_count": 1,
        "total_bytes": len(summary_bytes) + len(events_bytes),
        "received_at": response.json()["received_at"],
        "expires_at": None,
    }
    stored = await db_session.scalar(
        select(F12RawEvidenceCapture)
        .options(selectinload(F12RawEvidenceCapture.pairs))
        .where(F12RawEvidenceCapture.capture_id == manifest["capture_id"])
    )
    assert stored.profile_id == uuid.UUID(profile["id"])
    assert stored.device_id == uuid.UUID(device["id"])
    assert stored.pairs[0].summary_bytes == summary_bytes
    assert stored.pairs[0].events_bytes == events_bytes


@pytest.mark.asyncio
async def test_identical_retry_returns_the_same_stable_receipt(
    client,
    adult_session,
    monitoring_authorization,
):
    profile, device, headers = await authorized_device(
        client, adult_session, monitoring_authorization, "F12 idempotency"
    )
    manifest, summary_bytes, events_bytes = available_upload(
        profile["id"], device["id"]
    )
    request_files = lambda: multipart(manifest, summary_bytes, events_bytes)

    first = await client.post(endpoint(profile["id"]), headers=headers, files=request_files())
    repeated = await client.post(endpoint(profile["id"]), headers=headers, files=request_files())

    assert first.status_code == 201
    assert repeated.status_code == 200
    assert repeated.headers["x-f12-evidence-write-result"] == "unchanged"
    assert repeated.json() == first.json()


@pytest.mark.asyncio
async def test_capture_and_revision_conflicts_do_not_overwrite_originals(
    client,
    db_session,
    adult_session,
    monitoring_authorization,
):
    profile, device, headers = await authorized_device(
        client, adult_session, monitoring_authorization, "F12 conflicts"
    )
    first_manifest, first_summary, first_events = available_upload(
        profile["id"], device["id"]
    )
    first = await client.post(
        endpoint(profile["id"]),
        headers=headers,
        files=multipart(first_manifest, first_summary, first_events),
    )
    assert first.status_code == 201

    changed_manifest, changed_summary, changed_events = available_upload(
        profile["id"], device["id"]
    )
    changed_manifest["capture_id"] = first_manifest["capture_id"]
    reused_id = await client.post(
        endpoint(profile["id"]),
        headers=headers,
        files=multipart(changed_manifest, changed_summary, changed_events),
    )
    assert reused_id.status_code == 409
    assert reused_id.json()["detail"]["code"] == "capture_id_conflict"

    changed_manifest["capture_id"] = capture_id(
        profile["id"],
        device["id"],
        date.fromisoformat(changed_manifest["local_date"]),
        1,
        changed_manifest["content_sha256"],
    )
    reused_revision = await client.post(
        endpoint(profile["id"]),
        headers=headers,
        files=multipart(changed_manifest, changed_summary, changed_events),
    )
    assert reused_revision.status_code == 409
    assert reused_revision.json()["detail"]["code"] == "evidence_revision_conflict"

    stored = await db_session.scalar(
        select(F12RawEvidenceCapture)
        .options(selectinload(F12RawEvidenceCapture.pairs))
        .where(F12RawEvidenceCapture.capture_id == first_manifest["capture_id"])
    )
    assert stored.pairs[0].summary_bytes == first_summary
    assert stored.pairs[0].events_bytes == first_events


@pytest.mark.asyncio
async def test_new_revision_preserves_previous_evidence(
    client,
    db_session,
    adult_session,
    monitoring_authorization,
):
    profile, device, headers = await authorized_device(
        client, adult_session, monitoring_authorization, "F12 revisions"
    )
    for revision in (1, 2):
        manifest, summary_bytes, events_bytes = available_upload(
            profile["id"], device["id"], revision=revision
        )
        response = await client.post(
            endpoint(profile["id"]),
            headers=headers,
            files=multipart(manifest, summary_bytes, events_bytes),
        )
        assert response.status_code == 201

    revisions = list(
        await db_session.scalars(
            select(F12RawEvidenceCapture.revision).order_by(
                F12RawEvidenceCapture.revision
            )
        )
    )
    assert revisions == [1, 2]


@pytest.mark.asyncio
async def test_authentication_monitoring_and_profile_isolation_are_enforced(
    client,
    adult_session,
    monitoring_authorization,
):
    allowed_profile, allowed_device, allowed_headers = await authorized_device(
        client, adult_session, monitoring_authorization, "F12 allowed"
    )
    other_profile, _, other_headers = await create_profile_and_device(
        client, "F12 not authorized"
    )
    manifest, summary_bytes, events_bytes = available_upload(
        allowed_profile["id"], allowed_device["id"]
    )

    missing_auth = await client.post(
        endpoint(allowed_profile["id"]),
        files=multipart(manifest, summary_bytes, events_bytes),
    )
    missing_consent = await client.post(
        endpoint(other_profile["id"]),
        headers=other_headers,
        files=multipart(manifest, summary_bytes, events_bytes),
    )
    wrong_profile = await client.post(
        endpoint(other_profile["id"]),
        headers=allowed_headers,
        files=multipart(manifest, summary_bytes, events_bytes),
    )

    assert missing_auth.status_code == 401
    assert missing_consent.status_code == 403
    assert missing_consent.json()["detail"] == "Monitoring authorization is not active"
    assert wrong_profile.status_code == 403
    assert wrong_profile.json()["detail"] == "Profile access denied"


@pytest.mark.asyncio
async def test_hash_contract_date_and_size_failures_are_controlled(
    client,
    adult_session,
    monitoring_authorization,
    monkeypatch,
):
    profile, device, headers = await authorized_device(
        client, adult_session, monitoring_authorization, "F12 invalid"
    )
    manifest, summary_bytes, events_bytes = available_upload(
        profile["id"], device["id"]
    )

    bad_hash = json.loads(json.dumps(manifest))
    bad_hash["pairs"][0]["events_sha256"] = "0" * 64
    integrity = await client.post(
        endpoint(profile["id"]),
        headers=headers,
        files=multipart(bad_hash, summary_bytes, events_bytes),
    )
    assert integrity.status_code == 422
    assert integrity.json()["detail"]["code"] == "events_hash_mismatch"

    bad_schema_events = events_bytes.replace(b"raw-usage-event-v1", b"raw-usage-event-v2")
    bad_schema = json.loads(json.dumps(manifest))
    bad_schema["pairs"][0]["events_sha256"] = sha256(bad_schema_events)
    digest = hashlib.sha256(summary_bytes + b"\0" + bad_schema_events + b"\0").hexdigest()
    bad_schema["content_sha256"] = digest
    bad_schema["capture_id"] = capture_id(
        profile["id"], device["id"], date(2026, 11, 18), 1, digest
    )
    contract = await client.post(
        endpoint(profile["id"]),
        headers=headers,
        files=multipart(bad_schema, summary_bytes, bad_schema_events),
    )
    assert contract.status_code == 422
    assert contract.json()["detail"]["code"] == "unsupported_event_schema"

    invalid_zone = json.loads(json.dumps(manifest))
    invalid_zone["time_zone"] = "Not/A_Real_Zone"
    zone = await client.post(
        endpoint(profile["id"]),
        headers=headers,
        files=multipart(invalid_zone, summary_bytes, events_bytes),
    )
    assert zone.status_code == 422
    assert zone.json()["detail"]["code"] == "invalid_manifest"

    wrong_day = json.loads(json.dumps(manifest))
    wrong_day["local_date"] = "2026-11-17"
    wrong_day["capture_id"] = capture_id(
        profile["id"], device["id"], date(2026, 11, 17), 1, manifest["content_sha256"]
    )
    day_binding = await client.post(
        endpoint(profile["id"]),
        headers=headers,
        files=multipart(wrong_day, summary_bytes, events_bytes),
    )
    assert day_binding.status_code == 422
    assert day_binding.json()["detail"]["code"] == "day_query_missing"

    monkeypatch.setattr(profile_f12_evidence, "F12_MAX_HTTP_BODY_BYTES", 128)
    too_large = await client.post(
        endpoint(profile["id"]),
        headers=headers,
        files=multipart(manifest, summary_bytes, events_bytes),
    )
    assert too_large.status_code == 413
    assert too_large.json()["detail"]["code"] == "request_too_large"


@pytest.mark.asyncio
async def test_unavailable_is_persisted_without_files_or_zero_observation(
    client,
    db_session,
    adult_session,
    monitoring_authorization,
):
    profile, device, headers = await authorized_device(
        client, adult_session, monitoring_authorization, "F12 unavailable"
    )
    manifest = unavailable_upload(profile["id"], device["id"])

    response = await client.post(
        endpoint(profile["id"]),
        headers=headers,
        files=multipart(manifest),
    )

    assert response.status_code == 201
    assert response.json()["evidence_state"] == "UNAVAILABLE"
    assert response.json()["pair_count"] == 0
    assert response.json()["total_bytes"] == 0
    stored = await db_session.scalar(select(F12RawEvidenceCapture))
    assert stored.unavailable_reason == "NOT_EXPORTED"
    assert stored.pairs == []


@pytest.mark.asyncio
async def test_database_failure_rolls_back_capture_and_pairs(
    client,
    db_session,
    adult_session,
    monitoring_authorization,
    monkeypatch,
):
    profile, device, headers = await authorized_device(
        client, adult_session, monitoring_authorization, "F12 rollback"
    )
    manifest, summary_bytes, events_bytes = available_upload(
        profile["id"], device["id"]
    )

    async def fail_commit(_repository):
        raise OperationalError("COMMIT", {}, RuntimeError("synthetic storage failure"))

    monkeypatch.setattr(F12EvidenceRepository, "commit", fail_commit)
    response = await client.post(
        endpoint(profile["id"]),
        headers=headers,
        files=multipart(manifest, summary_bytes, events_bytes),
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "evidence_storage_unavailable"
    assert await db_session.scalar(
        select(func.count()).select_from(F12RawEvidenceCapture)
    ) == 0


@pytest.mark.asyncio
async def test_configured_retention_expires_old_evidence_transactionally(
    client,
    db_session,
    adult_session,
    monitoring_authorization,
    monkeypatch,
):
    profile, device, headers = await authorized_device(
        client, adult_session, monitoring_authorization, "F12 retention"
    )
    monkeypatch.setattr(
        profile_f12_evidence,
        "get_settings",
        lambda: SimpleNamespace(f12_evidence_retention_days=1),
    )
    first_manifest, first_summary, first_events = available_upload(
        profile["id"], device["id"], revision=1
    )
    first = await client.post(
        endpoint(profile["id"]),
        headers=headers,
        files=multipart(first_manifest, first_summary, first_events),
    )
    assert first.status_code == 201
    stored = await db_session.scalar(
        select(F12RawEvidenceCapture).where(
            F12RawEvidenceCapture.capture_id == first_manifest["capture_id"]
        )
    )
    assert stored.expires_at is not None
    stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()

    second_manifest, second_summary, second_events = available_upload(
        profile["id"], device["id"], revision=2
    )
    second = await client.post(
        endpoint(profile["id"]),
        headers=headers,
        files=multipart(second_manifest, second_summary, second_events),
    )

    assert second.status_code == 201
    captures = list(await db_session.scalars(select(F12RawEvidenceCapture)))
    assert [capture.revision for capture in captures] == [2]
