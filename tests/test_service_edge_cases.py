import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import ActivityInterest
from app.schemas.activity_suggestion import ActivitySuggestionContext
from app.schemas.device import DeviceCreate
from app.schemas.profile_activity_preferences import (
    ProfileActivityPreferencesRead,
    ProfileActivityPreferencesUpdate,
)
from app.services.activity_selector_gateway import ActivitySelectorResult, SelectorActivity
from app.services.activity_suggestion_service import ActivitySuggestionService
from app.services.child_offer_service import (
    ChildOfferOperationConflict,
    ChildOfferOperationPending,
    ChildOfferService,
)
from app.services.child_offers_client import ChildOffersError
from app.services.device_service import (
    DeviceAlreadyLinkedToAnotherProfileError,
    DeviceNotFoundError,
    DeviceService,
    InactiveProfileError,
    is_installation_id_conflict,
)
from app.services.profile_activity_preferences_service import (
    ProfileActivityPreferencesService,
)


class OperationDb:
    def __init__(self, scalar_results, operations=None):
        self.scalar_results = list(scalar_results)
        self.operations = operations or {}
        self.commits = 0
        self.rollbacks = 0

    async def scalar(self, _statement):
        return self.scalar_results.pop(0)

    async def get(self, _model, request_id):
        return self.operations.get(request_id)

    def add(self, operation):
        self.operations[operation.request_id] = operation

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class CoordinatorStub:
    def __init__(self, *, error=None, history=()):
        self.error = error
        self.history_offers = list(history)
        self.calls = []

    async def evaluate(self, payload):
        return await self._reply("evaluate", payload)

    async def evaluate_adaptive(self, payload):
        return await self._reply("adaptive", payload)

    async def event(self, payload):
        return await self._reply("event", payload)

    async def history(self, profile_id):
        self.calls.append(("history", profile_id))
        return {"offers": self.history_offers}

    async def _reply(self, kind, payload):
        self.calls.append((kind, payload))
        if self.error is not None:
            raise self.error
        return {"request_id": payload["request_id"], "status": "accepted"}


def operation_payload(request_id="request-1", offer_id="offer-1"):
    return {"request_id": request_id, "offer_id": offer_id}


@pytest.mark.asyncio
async def test_child_offer_service_persists_success_and_replays_exact_request():
    profile_id = uuid.uuid4()
    device_id = uuid.uuid4()
    payload = operation_payload()
    db = OperationDb(
        [SimpleNamespace(active=True), None, SimpleNamespace(active=True)]
    )
    coordinator = CoordinatorStub()
    service = ChildOfferService(db, coordinator)

    receipt = await service.evaluate_adaptive(profile_id, device_id, payload)
    replay = await service.evaluate_adaptive(profile_id, device_id, payload)

    assert receipt == {"request_id": "request-1", "status": "accepted"}
    assert replay == {**receipt, "replayed": True}
    assert coordinator.calls == [("adaptive", payload)]
    assert db.operations["request-1"].status == "DONE"
    assert db.commits == 2
    assert db.rollbacks == 1


@pytest.mark.asyncio
async def test_child_offer_service_rejects_inactive_conflicting_and_pending_requests():
    profile_id = uuid.uuid4()
    device_id = uuid.uuid4()
    payload = operation_payload()

    inactive = ChildOfferService(OperationDb([SimpleNamespace(active=False)]), CoordinatorStub())
    with pytest.raises(ChildOfferOperationConflict):
        await inactive.evaluate(profile_id, device_id, payload)

    existing = SimpleNamespace(
        profile_id=profile_id,
        device_id=device_id,
        kind="evaluate",
        payload={**payload, "offer_id": "different"},
        status="PENDING",
    )
    conflict_db = OperationDb([SimpleNamespace(active=True)], {"request-1": existing})
    with pytest.raises(ChildOfferOperationConflict):
        await ChildOfferService(conflict_db, CoordinatorStub()).evaluate(
            profile_id, device_id, payload
        )
    assert conflict_db.rollbacks == 1

    pending_db = OperationDb([SimpleNamespace(active=True), "older-request"])
    with pytest.raises(ChildOfferOperationPending):
        await ChildOfferService(pending_db, CoordinatorStub()).evaluate(
            profile_id, device_id, payload
        )
    assert pending_db.rollbacks == 1


@pytest.mark.asyncio
async def test_child_offer_service_validates_event_ownership_through_history():
    profile_id = uuid.uuid4()
    device_id = uuid.uuid4()
    payload = operation_payload()
    rejected = ChildOfferService(OperationDb([]), CoordinatorStub(history=[]))

    with pytest.raises(ChildOfferOperationConflict):
        await rejected.event(profile_id, device_id, payload)

    db = OperationDb([SimpleNamespace(active=True), None])
    coordinator = CoordinatorStub(history=[{"offer_id": "offer-1"}])
    receipt = await ChildOfferService(db, coordinator).event(profile_id, device_id, payload)

    assert receipt["status"] == "accepted"
    assert [call[0] for call in coordinator.calls] == ["history", "event"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 409])
async def test_child_offer_service_marks_definitive_upstream_rejections_as_conflicts(status_code):
    profile_id = uuid.uuid4()
    payload = operation_payload()
    db = OperationDb([SimpleNamespace(active=True), None])
    service = ChildOfferService(
        db, CoordinatorStub(error=ChildOffersError("rejected", status_code))
    )

    with pytest.raises(ChildOffersError):
        await service.evaluate(profile_id, uuid.uuid4(), payload)

    assert db.operations["request-1"].status == "CONFLICT"
    assert db.commits == 2


@pytest.mark.asyncio
async def test_child_offer_service_keeps_uncertain_upstream_failures_pending():
    db = OperationDb([SimpleNamespace(active=True), None])
    service = ChildOfferService(db, CoordinatorStub(error=ChildOffersError("timeout", 504)))

    with pytest.raises(ChildOffersError):
        await service.evaluate(uuid.uuid4(), uuid.uuid4(), operation_payload())

    assert db.operations["request-1"].status == "PENDING"
    assert db.commits == 1


def device_service(repository):
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    service = DeviceService(db, repository)
    service.link_codes = SimpleNamespace(revoke_pending_for_profile=AsyncMock(return_value=2))
    service.audit = SimpleNamespace(append=AsyncMock())
    return service, db


@pytest.mark.asyncio
async def test_device_service_deactivates_once_and_audits_the_change():
    device = SimpleNamespace(id=uuid.uuid4(), active=False)
    repository = SimpleNamespace(
        deactivate_if_active=AsyncMock(return_value=True),
        get_for_profile=AsyncMock(return_value=device),
    )
    service, db = device_service(repository)
    actor = SimpleNamespace(id=uuid.uuid4())

    result = await service.deactivate(uuid.uuid4(), device.id, actor)

    assert result is device
    service.link_codes.revoke_pending_for_profile.assert_awaited_once()
    service.audit.append.assert_awaited_once()
    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_device_service_rolls_back_when_deactivation_target_does_not_exist():
    repository = SimpleNamespace(
        deactivate_if_active=AsyncMock(return_value=False),
        get_for_profile=AsyncMock(return_value=None),
    )
    service, db = device_service(repository)

    with pytest.raises(DeviceNotFoundError):
        await service.deactivate(uuid.uuid4(), uuid.uuid4(), SimpleNamespace(id=uuid.uuid4()))

    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_device_service_ensure_link_handles_profile_and_installation_states():
    profile = SimpleNamespace(id=uuid.uuid4(), active=True)
    data = DeviceCreate(installation_id="installation-1", platform="ANDROID")
    existing = SimpleNamespace(profile_id=profile.id)
    repository = SimpleNamespace(
        get_by_installation_id=AsyncMock(return_value=existing),
        create=AsyncMock(),
    )
    service, _ = device_service(repository)

    reused = await service.ensure_link(profile, data)
    assert reused.device is existing and reused.created is False

    profile.active = False
    with pytest.raises(InactiveProfileError):
        await service.ensure_link(profile, data)

    profile.active = True
    existing.profile_id = uuid.uuid4()
    with pytest.raises(DeviceAlreadyLinkedToAnotherProfileError):
        await service.ensure_link(profile, data)

    created = SimpleNamespace(profile_id=profile.id)
    repository.get_by_installation_id.return_value = None
    repository.create.return_value = created
    registration = await service.ensure_link(profile, data)
    assert registration.device is created and registration.created is True


@pytest.mark.asyncio
async def test_device_service_recovers_from_concurrent_installation_registration():
    profile = SimpleNamespace(id=uuid.uuid4(), active=True)
    data = DeviceCreate(installation_id="installation-race", platform="ANDROID")
    existing = SimpleNamespace(profile_id=profile.id)
    original = SimpleNamespace(diag=SimpleNamespace(constraint_name="uq_devices_installation_id"))
    integrity_error = IntegrityError("insert", {}, original)
    repository = SimpleNamespace(
        get_by_installation_id=AsyncMock(side_effect=[None, existing]),
        create=AsyncMock(side_effect=integrity_error),
    )
    service, db = device_service(repository)

    registration = await service.register(profile, data)

    assert registration.device is existing and registration.created is False
    db.rollback.assert_awaited_once()


def test_installation_conflict_detection_supports_sqlite_style_messages():
    original = Exception("UNIQUE constraint failed: devices.installation_id")
    assert is_installation_id_conflict(IntegrityError("insert", {}, original)) is True
    assert is_installation_id_conflict(IntegrityError("insert", {}, Exception("other"))) is False


@pytest.mark.asyncio
async def test_profile_activity_preferences_service_defaults_and_upserts():
    profile_id = uuid.uuid4()
    stored = ProfileActivityPreferencesRead(
        profile_id=profile_id,
        interests=[ActivityInterest.MUSIC],
        considerations_answered=False,
    )
    repository = SimpleNamespace(
        get=AsyncMock(side_effect=[None, stored, None, stored]),
        upsert=AsyncMock(return_value=stored),
    )
    service = ProfileActivityPreferencesService(repository)

    default = await service.get(profile_id)
    persisted = await service.get(profile_id)
    created = await service.upsert(
        profile_id,
        ProfileActivityPreferencesUpdate(
            interests=[ActivityInterest.MUSIC], considerations_answered=False
        ),
    )
    updated = await service.upsert(
        profile_id,
        ProfileActivityPreferencesUpdate(considerations_answered=False),
    )

    assert default.profile_id == profile_id and default.interests == []
    assert persisted.interests == [ActivityInterest.MUSIC]
    assert created.interests == [ActivityInterest.MUSIC]
    assert updated.profile_id == profile_id
    assert repository.upsert.await_count == 2
    assert repository.upsert.await_args_list[-1].args[-1] == []


def selector_activity(index):
    return SelectorActivity(
        activity_id=f"activity-{index}",
        title=f"Activity {index}",
        description=f"Description {index}",
        category="creative",
        activity_level="calm",
        bedtime_suitable=True,
        requires_other_person=False,
        may_require_device_interaction=False,
    )


@pytest.mark.asyncio
async def test_activity_suggestion_service_limits_and_persists_selector_results():
    profile_id = uuid.uuid4()
    device_id = uuid.uuid4()
    selector = SimpleNamespace(
        recommend=AsyncMock(
            return_value=ActivitySelectorResult(
                activities=tuple(selector_activity(index) for index in range(4)),
                reason="ranked",
            )
        )
    )
    offers = SimpleNamespace(create_offer=AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4())))
    service = ActivitySuggestionService(
        SimpleNamespace(get=AsyncMock(return_value=None)), offers, selector
    )

    result = await service.suggest(
        profile_id, device_id, ActivitySuggestionContext.VOLUNTARY
    )

    assert len(result.activities) == 3
    assert result.offer_id is not None
    assert result.reason == "ranked"
    offers.create_offer.assert_awaited_once()


@pytest.mark.asyncio
async def test_activity_suggestion_service_does_not_persist_empty_results():
    offers = SimpleNamespace(create_offer=AsyncMock())
    selector = SimpleNamespace(
        recommend=AsyncMock(return_value=ActivitySelectorResult(activities=(), reason="none"))
    )
    service = ActivitySuggestionService(
        SimpleNamespace(
            get=AsyncMock(
                return_value=ProfileActivityPreferencesRead(profile_id=uuid.uuid4())
            )
        ),
        offers,
        selector,
    )

    result = await service.suggest(
        uuid.uuid4(), uuid.uuid4(), ActivitySuggestionContext.BEDTIME
    )

    assert result.offer_id is None and result.activities == []
    offers.create_offer.assert_not_awaited()
