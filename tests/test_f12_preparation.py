import hashlib
import json
import uuid
from copy import deepcopy
from datetime import UTC, date, datetime
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.db.models import (
    F12EvidencePreparation,
    F12RawEvidenceCapture,
    F12RawEvidencePair,
    HistoricalSubmission,
    HistoricalSubmissionStatus,
)
from app.repositories.f12_preparation_repository import F12PreparationRepository
from app.schemas.f12_evidence import F12_EXPORT_ADAPTER_VERSION
from app.schemas.historical_submission import (
    HISTORICAL_BRIDGE_VERSION,
    HistoricalSubmissionCreate,
    HistoricalSubmissionSummaryRead,
)
from app.services.f12_preparation_service import (
    F12PreparationAccessDeniedError,
    F12PreparationContractError,
    F12PreparationIneligibleError,
    F12PreparationService,
)
from app.services.f12_published_runner import run_published_preparer


FIXTURES = Path(__file__).parent / "fixtures" / "f12_contract_20261121"
VENDOR = Path(__file__).parents[1] / "vendor" / "modera_f12_94ff3a8"


def _fixture_json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_bytes())


def _fixture_pair() -> tuple[bytes, bytes]:
    return (
        (FIXTURES / "originals" / "summary.json").read_bytes(),
        (FIXTURES / "originals" / "events.jsonl").read_bytes(),
    )


async def _profile_and_device(client):
    created = await client.post(
        "/api/v1/profiles",
        json={"alias": "F12 preparation", "age": 9, "avatar": "fox"},
    )
    assert created.status_code == 201
    profile = created.json()
    registered = await client.post(
        f"/api/v1/profiles/{profile['id']}/devices",
        headers={"Authorization": f"Bearer {profile['bootstrap_token']}"},
        json={
            "installation_id": f"f12-preparation-{uuid.uuid4()}",
            "platform": "ANDROID",
        },
    )
    assert registered.status_code == 201
    return uuid.UUID(profile["id"]), uuid.UUID(registered.json()["id"])


async def _store_fixture_evidence(
    client,
    db_session,
    *,
    revision: int = 1,
    evidence_state: str = "AVAILABLE",
    corrupt: bool = False,
) -> F12RawEvidenceCapture:
    profile_id, device_id = await _profile_and_device(client)
    summary_bytes, events_bytes = _fixture_pair()
    if corrupt:
        events_bytes += b"not-json\n"
    summary = json.loads(summary_bytes)
    combined = hashlib.sha256(summary_bytes + b"\0" + events_bytes + b"\0").hexdigest()
    capture = F12RawEvidenceCapture(
        capture_id=hashlib.sha256(
            f"{profile_id}:{device_id}:{revision}:{combined}".encode()
        ).hexdigest(),
        profile_id=profile_id,
        device_id=device_id,
        local_date=date(2026, 9, 1),
        time_zone="America/Argentina/Buenos_Aires",
        revision=revision,
        envelope_version=1,
        adapter_version=F12_EXPORT_ADAPTER_VERSION,
        evidence_state=evidence_state,
        unavailable_reason="NOT_EXPORTED" if evidence_state == "UNAVAILABLE" else None,
        content_sha256=combined,
        pair_count=0 if evidence_state == "UNAVAILABLE" else 1,
        total_bytes=(
            0 if evidence_state == "UNAVAILABLE" else len(summary_bytes) + len(events_bytes)
        ),
        source_created_at=datetime(2026, 9, 2, 3, 2, tzinfo=UTC),
        receipt_status="RECEIVED",
        received_at=datetime(2026, 9, 2, 3, 3, tzinfo=UTC),
    )
    if evidence_state == "AVAILABLE":
        capture.pairs = [
            F12RawEvidencePair(
                ordinal=0,
                collector_run_id=summary["collectorRunId"],
                summary_sha256=hashlib.sha256(summary_bytes).hexdigest(),
                events_sha256=hashlib.sha256(events_bytes).hexdigest(),
                summary_size=len(summary_bytes),
                events_size=len(events_bytes),
                summary_bytes=summary_bytes,
                events_bytes=events_bytes,
            )
        ]
    db_session.add(capture)
    await db_session.commit()
    return capture


def test_vendored_preparer_sources_match_published_commit_hashes():
    expected = {}
    for line in (VENDOR / "SHA256SUMS").read_text(encoding="ascii").splitlines():
        digest, name = line.split("  ", 1)
        expected[name] = digest
    assert expected
    for name, digest in expected.items():
        assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == digest


def test_zip_fixture_has_semantic_parity_and_versioned_2026_5_identity():
    binding = _fixture_json("binding.synthetic.json")
    result = run_published_preparer(binding, [_fixture_pair()])
    actual = result["request"]
    expected = _fixture_json("historical-evaluate.synthetic.json")

    assert metadata.version("tzdata") == "2026.5"
    assert actual["representation"]["subject_id"] == (
        "series-9f9f5fd816eb85931f4a5e5d823758a3c4d18390be5ddb49ce89f9c1b679a7b0"
    )
    assert actual["representation"]["representation_record_id"] == (
        "rep-9973badeae7056a6a0449eb4985715231831cfbb8d8b228e9306cd84c9f2f78f"
    )
    assert actual["representation"]["provenance"]["input_fingerprint"] == (
        "c73793f6ef31903608234b1a206c69c32e2e7510db84ed4e47b6e53bcb3da9a6"
    )
    assert actual["representation"]["subject_id"] != expected["representation"]["subject_id"]

    normalized = deepcopy(actual)
    normalized_expected = deepcopy(expected)
    for document in (normalized, normalized_expected):
        representation = document["representation"]
        representation["subject_id"] = "<tzdata-versioned>"
        representation["representation_record_id"] = "<tzdata-versioned>"
        representation["provenance"]["input_fingerprint"] = "<tzdata-versioned>"
    assert normalized == normalized_expected

    expected_audit = _fixture_json("export-audit.synthetic.json")
    actual_audit = deepcopy(result["audit"])
    actual_audit["projection_fingerprint"] = "<tzdata-versioned>"
    expected_audit["projection_fingerprint"] = "<tzdata-versioned>"
    assert actual_audit == expected_audit


def test_historical_contract_accepts_only_both_published_provenances():
    body = _fixture_json("historical-submission.synthetic.json")
    exported = HistoricalSubmissionCreate.model_validate(body)
    assert exported.representation.provenance.source_version == F12_EXPORT_ADAPTER_VERSION

    legacy = deepcopy(body)
    legacy["representation"]["provenance"]["source_version"] = HISTORICAL_BRIDGE_VERSION
    HistoricalSubmissionCreate.model_validate(legacy)

    unknown = deepcopy(body)
    unknown["representation"]["provenance"]["source_version"] = "unknown-adapter"
    with pytest.raises(ValidationError):
        HistoricalSubmissionCreate.model_validate(unknown)


def test_zip_c1_fixture_preserves_abstention_as_distinct_outcome():
    response = _fixture_json("c1-response.synthetic.json")
    submission = SimpleNamespace(
        id=uuid.uuid4(),
        local_date=date(2026, 9, 1),
        status=HistoricalSubmissionStatus.PROCESSED,
        attempts=1,
        analytics_evaluation_id=response["result"]["evaluation"]["evaluation_id"],
        analytics_response=response,
        retryable=False,
        last_error_code=None,
        last_error_message=None,
    )
    summary = HistoricalSubmissionSummaryRead.from_submission(submission)
    assert summary.analytics_evaluation.outcome == "ABSTAIN"
    assert summary.analytics_evaluation.abstention_reason == "INSUFFICIENT_HISTORY"


@pytest.mark.asyncio
async def test_service_prepares_persists_audit_and_is_idempotent(
    client,
    db_session,
):
    evidence = await _store_fixture_evidence(client, db_session)
    original_summary = bytes(evidence.pairs[0].summary_bytes)
    original_events = bytes(evidence.pairs[0].events_bytes)
    service = F12PreparationService(F12PreparationRepository(db_session))

    first = await service.prepare(
        evidence_id=evidence.id,
        profile_id=evidence.profile_id,
        device_id=evidence.device_id,
    )
    second = await service.prepare(
        evidence_id=evidence.id,
        profile_id=evidence.profile_id,
        device_id=evidence.device_id,
    )

    assert second.id == first.id
    assert first.evidence_id == evidence.id
    assert first.evidence_content_sha256 == evidence.content_sha256
    assert first.submission_body["measurement_version"] == "capture-test-v1"
    assert first.submission_body["time_zone"] == evidence.time_zone
    assert first.submission_body["representation"]["provenance"]["source_version"] == (
        F12_EXPORT_ADAPTER_VERSION
    )
    assert first.audit["originals"][0]["summary_sha256"] == (
        hashlib.sha256(original_summary).hexdigest()
    )
    assert evidence.pairs[0].summary_bytes == original_summary
    assert evidence.pairs[0].events_bytes == original_events
    assert await db_session.scalar(select(func.count(F12EvidencePreparation.id))) == 1
    assert await db_session.scalar(select(func.count(HistoricalSubmission.id))) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("revision", "state", "code"),
    [
        (2, "AVAILABLE", "corrected_revision_policy_undefined"),
        (1, "UNAVAILABLE", "unavailable_policy_undefined"),
    ],
)
async def test_service_does_not_prepare_corrections_or_unavailable_as_zero(
    client,
    db_session,
    revision,
    state,
    code,
):
    evidence = await _store_fixture_evidence(
        client,
        db_session,
        revision=revision,
        evidence_state=state,
    )
    service = F12PreparationService(F12PreparationRepository(db_session))

    with pytest.raises(F12PreparationIneligibleError) as exc:
        await service.prepare(
            evidence_id=evidence.id,
            profile_id=evidence.profile_id,
            device_id=evidence.device_id,
        )
    assert exc.value.code == code
    assert await db_session.scalar(select(func.count(F12EvidencePreparation.id))) == 0


@pytest.mark.asyncio
async def test_service_rejects_contractually_invalid_persisted_bytes(
    client,
    db_session,
):
    evidence = await _store_fixture_evidence(client, db_session, corrupt=True)
    service = F12PreparationService(F12PreparationRepository(db_session))

    with pytest.raises(F12PreparationContractError):
        await service.prepare(
            evidence_id=evidence.id,
            profile_id=evidence.profile_id,
            device_id=evidence.device_id,
        )
    assert await db_session.scalar(select(func.count(F12EvidencePreparation.id))) == 0


@pytest.mark.asyncio
async def test_service_requires_the_authenticated_evidence_owner(
    client,
    db_session,
):
    evidence = await _store_fixture_evidence(client, db_session)
    service = F12PreparationService(F12PreparationRepository(db_session))

    with pytest.raises(F12PreparationAccessDeniedError):
        await service.prepare(
            evidence_id=evidence.id,
            profile_id=uuid.uuid4(),
            device_id=evidence.device_id,
        )
    assert await db_session.scalar(select(func.count(F12EvidencePreparation.id))) == 0
