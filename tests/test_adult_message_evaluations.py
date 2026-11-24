"""Controlled fictitious producer for Analytics adult-messages v1."""

import asyncio
import copy
import hashlib
import json
from pathlib import Path
import uuid

import httpx
import pytest
from pydantic import ValidationError

from app.api.routes.profile_adult_messages import get_adult_messages_client
from app.db.models import (
    AdultMessageEvaluationJobStatus,
    Profile,
)
from app.main import app
from app.repositories.adult_message_evaluation_repository import (
    AdultMessageEvaluationRepository,
)
from app.schemas.adult_message import ControlledAdultMessageFixture
from app.services.adult_message_evaluation_service import (
    AdultMessageEvaluationContentConflictError,
    AdultMessageEvaluationPendingError,
    AdultMessageEvaluationRetryError,
    AdultMessageEvaluationService,
)
from app.services.adult_messages_client import AdultMessagesClient
from app.services.representative_verification_service import (
    RepresentativeVerificationService,
)


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "adult_message_evaluation.fictitious.json"
)
TOKEN = "fictitious-adult-message-service-token"


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def configuration():
    return {
        "c1_role": "complementary_technical_evidence",
        "cooldown_days": 14,
        "increase_minutes": 20,
        "limit_period_days": 28,
        "maximum_episode_days": 56,
        "maximum_new_cards": 1,
        "minimum_direction_days": 10,
        "pmum_role": "none",
        "return_margin_minutes": 5,
        "scope": "internal_candidate_not_family_validated",
        "version": "adult-messages-internal-candidate-v1",
        "window_days": 14,
    }


class ControlledAnalytics:
    """Stateful HTTP double that follows the published receipt/history contract."""

    def __init__(self):
        self.calls = []
        self.receipts = {}
        self.histories = {}
        self.lose_first_response = set()
        self.lost = set()
        self.http_failures = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        if request.method == "GET":
            profile_id = request.url.path.split("/")[-2]
            return httpx.Response(
                200, json={"versions": copy.deepcopy(self.histories.get(profile_id, []))}
            )
        assert request.method == "POST"
        assert request.url.path == "/v1/adult-messages/evaluate"
        payload = json.loads(request.content)
        self.calls.append(copy.deepcopy(payload))
        request_id = payload["request_id"]
        failure = self.http_failures.get(request_id)
        if failure:
            status = failure.pop(0)
            if status is not None:
                return httpx.Response(status, json={"error": f"controlled_http_{status}"})

        fingerprint = canonical({k: v for k, v in payload.items() if k != "request_id"})
        previous = self.receipts.get(request_id)
        if previous is not None:
            if previous[0] != fingerprint:
                return httpx.Response(409, json={"error": "request_content_conflict"})
            replay = copy.deepcopy(previous[1])
            replay["replayed"] = True
            return httpx.Response(200, json=replay)

        response = self._evaluate(payload)
        self.receipts[request_id] = (fingerprint, copy.deepcopy(response))
        if response["card"] is not None:
            self.histories.setdefault(payload["profile_id"], []).append(
                {"version": response["card_version"], "card": copy.deepcopy(response["card"])}
            )
        if request_id in self.lose_first_response and request_id not in self.lost:
            self.lost.add(request_id)
            raise httpx.ReadTimeout("controlled lost response", request=request)
        return httpx.Response(200, json=response)

    def _evaluate(self, payload):
        evaluation_id = hashlib.sha256(
            canonical({k: v for k, v in payload.items() if k != "request_id"}).encode()
        ).hexdigest()
        common = {
            "configuration": configuration(),
            "c1_evidence_ids": payload["c1_evidence_ids"],
            "c1_role": "complementary_technical_evidence",
            "push_notification": False,
            "historical_card_corrections": [],
            "replayed": False,
            "evaluation_id": evaluation_id,
        }
        if "insufficient" in payload["request_id"]:
            return {
                "status": "INSUFFICIENT_DATA",
                "action": "NONE",
                "card": None,
                "corrected_dates": [],
                "c1_correction_warning_since": None,
                "card_version": None,
                **common,
            }
        if "no-card" in payload["request_id"]:
            return {
                "status": "NO_CARD",
                "action": "NONE",
                "card": None,
                "corrected_dates": [],
                "c1_correction_warning_since": None,
                "card_version": None,
                **common,
            }

        corrected = [row["date"] for row in payload["days"] if row["revision"] > 1]
        withdrawn = bool(corrected)
        card_id = hashlib.sha256(
            f"{payload['profile_id']}:{payload['series_id']}:2026-02-02".encode()
        ).hexdigest()
        prior_versions = [
            item for item in self.histories.get(payload["profile_id"], [])
            if item["card"]["card_id"] == card_id
        ]
        version = len(prior_versions) + 1
        card = {
            "card_id": card_id,
            "profile_id": payload["profile_id"],
            "series_id": payload["series_id"],
            "direction": "INCREASE",
            "status": "WITHDRAWN" if withdrawn else "ACTIVE",
            "title": "Actualización del aviso" if withdrawn else "Más uso registrado en este teléfono",
            "text": (
                "Retiramos este aviso: una corrección cambió la comparación."
                if withdrawn
                else "La fixture controlada registró 40 min más por día."
            ),
            "recommendation": "Podés conversar sobre qué cambió en la rutina.",
            "statistics": None if withdrawn else {
                "recent": 140.0,
                "reference": 100.0,
                "delta": 40.0,
                "minimum": 40.0,
                "maximum": 40.0,
                "above": 14.0,
                "below": 0.0,
                "percent_change": 40.0,
                "observed_days_recent": 14,
                "observed_days_reference": 14,
            },
            "evaluation_date": payload["evaluation_date"],
            "time_zone": payload["time_zone"],
            "opening_reference_start": "2026-01-05",
            "opening_recent_start": "2026-01-19",
            "comparison_recent_start": "2026-01-19",
            "measurement": "uso registrado en este teléfono",
            "measurement_detail": "Duración de pantalla interactiva de una fixture ficticia.",
            "configuration_id": "adult-messages-internal-candidate-v1",
            "configuration_fingerprint": hashlib.sha256(b"controlled-config").hexdigest(),
            "scope": "internal_candidate_not_family_validated",
            "delivery_status": "CANDIDATE_NOT_DELIVERED",
        }
        if withdrawn:
            card["corrected_dates"] = corrected
            card["updated_date"] = payload["evaluation_date"]
        return {
            "status": card["status"],
            "action": "UPDATE" if withdrawn else "CREATE",
            "card": card,
            "corrected_dates": corrected,
            "c1_correction_warning_since": min(corrected) if corrected else None,
            "card_version": version,
            **common,
        }


def fixture_for(profile_id: uuid.UUID, scenario="opening"):
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw["fixture_id"] = f"fictitious-{scenario}-{profile_id}"
    raw["payload"]["request_id"] = f"adult-msg-fictitious-{scenario}-{profile_id}"
    raw["payload"]["profile_id"] = str(profile_id)
    raw["payload"]["series_id"] = f"series-fictitious-{profile_id}"
    if scenario == "no-card":
        for row in raw["payload"]["days"][14:]:
            row["minutes"] = 100
    elif scenario == "insufficient":
        row = raw["payload"]["days"][-1]
        row.update(minutes=None, observed=False, quality_verified=False)
    elif scenario == "correction":
        for row in raw["payload"]["days"][14:]:
            row["revision"] = 2
            row["minutes"] = 100
    return ControlledAdultMessageFixture.model_validate(raw)


async def add_profile(db_session, profile_id=None):
    profile = Profile(
        id=profile_id or uuid.uuid4(),
        alias="Fictitious adult-message fixture",
        age=9,
        avatar="fox",
        active=True,
    )
    db_session.add(profile)
    await db_session.commit()
    return profile


def make_service(db_session, analytics):
    client = AdultMessagesClient(
        "http://analytics.test", TOKEN, 2.5,
        transport=httpx.MockTransport(analytics),
    )
    return AdultMessageEvaluationService(
        AdultMessageEvaluationRepository(db_session), client
    ), client


def test_fixture_wrapper_and_exact_28_day_contract():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fixture = ControlledAdultMessageFixture.model_validate(raw)
    assert fixture.fictional is True
    assert len(fixture.payload.days) == 28
    assert fixture.payload.c1_evidence_ids == []
    for mutation in ("fictional", "days", "monday", "extra"):
        invalid = copy.deepcopy(raw)
        if mutation == "fictional":
            invalid["fictional"] = False
        elif mutation == "days":
            invalid["payload"]["days"].pop()
        elif mutation == "monday":
            invalid["payload"]["evaluation_date"] = "2026-02-03"
        else:
            invalid["payload"]["inferred_from_usage"] = True
        with pytest.raises(ValidationError):
            ControlledAdultMessageFixture.model_validate(invalid)


@pytest.mark.asyncio
async def test_create_is_durable_before_send_and_create_active(db_session):
    profile = await add_profile(db_session)
    analytics = ControlledAnalytics()
    service, _ = make_service(db_session, analytics)
    fixture = fixture_for(profile.id)
    job = await service.create_from_fixture(fixture)
    assert job.status == AdultMessageEvaluationJobStatus.PENDING
    assert job.attempts == 0
    assert analytics.calls == []
    assert job.payload == fixture.payload.model_dump(mode="json")
    assert len(job.payload_sha256) == 64

    job = await service.execute(job.id)
    assert job.status == AdultMessageEvaluationJobStatus.PROCESSED
    assert job.attempts == 1
    assert job.analytics_response["status"] == "ACTIVE"
    assert job.analytics_response["action"] == "CREATE"
    assert job.analytics_card_version == 1
    assert job.payload["c1_evidence_ids"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "expected"),
    [("no-card", "NO_CARD"), ("insufficient", "INSUFFICIENT_DATA")],
)
async def test_none_and_insufficient_are_processed_without_cards(
    db_session, scenario, expected
):
    profile = await add_profile(db_session)
    analytics = ControlledAnalytics()
    service, client = make_service(db_session, analytics)
    job = await service.create_and_execute(fixture_for(profile.id, scenario))
    assert job.status == AdultMessageEvaluationJobStatus.PROCESSED
    assert job.analytics_response["status"] == expected
    assert job.analytics_response["action"] == "NONE"
    assert job.analytics_response["card"] is None
    assert (await client.history(profile.id)).versions == []


@pytest.mark.asyncio
async def test_lost_response_retries_same_frozen_request_and_replays(db_session):
    profile = await add_profile(db_session)
    analytics = ControlledAnalytics()
    service, client = make_service(db_session, analytics)
    fixture = fixture_for(profile.id, "lost-response")
    analytics.lose_first_response.add(fixture.payload.request_id)
    job = await service.create_from_fixture(fixture)
    first = await service.execute(job.id)
    assert first.status == AdultMessageEvaluationJobStatus.TECHNICAL_ERROR
    assert first.retryable is True
    assert first.attempts == 1

    recovered = await service.execute(job.id, retry=True)
    assert recovered.status == AdultMessageEvaluationJobStatus.PROCESSED
    assert recovered.analytics_replayed is True
    assert recovered.attempts == 2
    assert analytics.calls[0] == analytics.calls[1] == recovered.payload
    history = await client.history(profile.id)
    assert len(history.versions) == 1


@pytest.mark.asyncio
async def test_same_request_with_changed_content_is_rejected_locally(db_session):
    profile = await add_profile(db_session)
    analytics = ControlledAnalytics()
    service, _ = make_service(db_session, analytics)
    original = fixture_for(profile.id)
    await service.create_from_fixture(original)
    changed = original.model_copy(deep=True)
    changed.payload.days[0].minutes = 101
    with pytest.raises(AdultMessageEvaluationContentConflictError):
        await service.create_from_fixture(changed)
    assert analytics.calls == []


@pytest.mark.asyncio
async def test_upstream_409_is_durable_and_not_retryable(db_session):
    profile = await add_profile(db_session)
    analytics = ControlledAnalytics()
    service, _ = make_service(db_session, analytics)
    fixture = fixture_for(profile.id, "upstream-conflict")
    analytics.http_failures[fixture.payload.request_id] = [409]
    job = await service.create_and_execute(fixture)
    assert job.status == AdultMessageEvaluationJobStatus.CONFLICT
    assert job.last_error_code == "controlled_http_409"
    assert job.retryable is False
    with pytest.raises(AdultMessageEvaluationRetryError):
        await service.execute(job.id, retry=True)


@pytest.mark.asyncio
async def test_retryable_and_permanent_http_errors(db_session):
    retry_profile = await add_profile(db_session)
    analytics = ControlledAnalytics()
    service, _ = make_service(db_session, analytics)
    retry_fixture = fixture_for(retry_profile.id, "http-503")
    analytics.http_failures[retry_fixture.payload.request_id] = [503, None]
    job = await service.create_and_execute(retry_fixture)
    assert job.status == AdultMessageEvaluationJobStatus.TECHNICAL_ERROR
    assert job.retryable is True
    job = await service.execute(job.id, retry=True)
    assert job.status == AdultMessageEvaluationJobStatus.PROCESSED

    permanent_profile = await add_profile(db_session)
    permanent_fixture = fixture_for(permanent_profile.id, "http-400")
    analytics.http_failures[permanent_fixture.payload.request_id] = [400]
    permanent = await service.create_and_execute(permanent_fixture)
    assert permanent.status == AdultMessageEvaluationJobStatus.TECHNICAL_ERROR
    assert permanent.retryable is False
    with pytest.raises(AdultMessageEvaluationRetryError):
        await service.execute(permanent.id, retry=True)


@pytest.mark.asyncio
async def test_concurrent_execute_posts_once(db_session):
    profile = await add_profile(db_session)
    analytics = ControlledAnalytics()
    service, _ = make_service(db_session, analytics)
    job = await service.create_from_fixture(fixture_for(profile.id, "concurrent"))
    first, second = await asyncio.gather(
        service.execute(job.id), service.execute(job.id)
    )
    assert first.id == second.id == job.id
    assert first.status == second.status == AdultMessageEvaluationJobStatus.PROCESSED
    assert len(analytics.calls) == 1


@pytest.mark.asyncio
async def test_unresolved_job_blocks_another_identity(db_session):
    profile = await add_profile(db_session)
    analytics = ControlledAnalytics()
    service, _ = make_service(db_session, analytics)
    await service.create_from_fixture(fixture_for(profile.id, "pending-one"))
    with pytest.raises(AdultMessageEvaluationPendingError):
        await service.create_from_fixture(fixture_for(profile.id, "pending-two"))


@pytest.mark.asyncio
async def test_correction_versions_and_authorized_get_never_resurrect_active(
    client, db_session, adult_session
):
    created = await client.post(
        "/api/v1/profiles",
        json={"alias": "Fictitious correction", "age": 9, "avatar": "fox"},
    )
    profile_id = uuid.UUID(created.json()["id"])
    headers, adult = await adult_session(profile_id)
    await RepresentativeVerificationService(db_session).approve(
        adult.id, profile_id, verified_by="pytest-fictitious"
    )
    analytics = ControlledAnalytics()
    service, analytics_client = make_service(db_session, analytics)
    opening = fixture_for(profile_id, "opening")
    first = await service.create_and_execute(opening)
    correction = fixture_for(profile_id, "correction")
    second = await service.create_and_execute(correction)
    assert first.analytics_card_id == second.analytics_card_id
    assert second.analytics_response["status"] == "WITHDRAWN"
    assert second.analytics_response["corrected_dates"] == [
        row.date.isoformat() for row in correction.payload.days[14:]
    ]
    assert second.analytics_card_version == 2

    replay = await analytics_client.evaluate(opening.payload.model_dump(mode="json"))
    assert replay.replayed is True
    assert replay.response["card"]["status"] == "ACTIVE"

    app.dependency_overrides[get_adult_messages_client] = lambda: analytics_client
    try:
        response = await client.get(
            f"/api/v1/profiles/{profile_id}/adult-messages/history",
            headers=headers,
        )
    finally:
        app.dependency_overrides.pop(get_adult_messages_client, None)
    assert response.status_code == 200
    versions = response.json()["versions"]
    assert [item["version"] for item in versions] == [1, 2]
    assert [item["card"]["status"] for item in versions] == ["ACTIVE", "WITHDRAWN"]
    latest = max(versions, key=lambda item: item["version"])
    assert latest["card"]["status"] == "WITHDRAWN"
    assert len(analytics.histories[str(profile_id)]) == 2


@pytest.mark.asyncio
async def test_correction_requires_new_request_and_greater_revision(db_session):
    profile = await add_profile(db_session)
    analytics = ControlledAnalytics()
    service, _ = make_service(db_session, analytics)
    await service.create_and_execute(fixture_for(profile.id, "opening"))
    invalid = fixture_for(profile.id, "same-revision-correction")
    invalid.payload.days[-1].minutes = 100
    with pytest.raises(AdultMessageEvaluationContentConflictError):
        await service.create_from_fixture(invalid)
