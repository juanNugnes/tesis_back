"""Automatic child-offer transport; legacy v1 evaluation remains test-gated."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    get_current_adult,
    get_current_device,
    require_device_profile_access,
    require_usage_monitoring_authorization,
    require_verified_profile_access,
)
from app.core.config import get_settings
from app.db.models import AdultAccount, Device, Profile
from app.db.session import get_db
from app.schemas.child_offer import (
    AutomaticOfferSettingsRead,
    AutomaticOfferSettingsUpdate,
    AutomaticOpportunityCreate,
    AutomaticOpportunityRead,
    ChildOfferEvaluation,
    ChildOfferEvaluationReceipt,
    ChildOfferEvent,
    ChildOfferEventReceipt,
    ChildOfferHistory,
)
from app.repositories.activity_offer_repository import ActivityOfferRepository
from app.repositories.automatic_offer_settings_repository import (
    AutomaticOfferSettingsRepository,
)
from app.repositories.experimental_pmum_repository import ExperimentalPmumRepository
from app.repositories.profile_activity_preferences_repository import (
    ProfileActivityPreferencesRepository,
)
from app.repositories.profile_settings_repository import ProfileSettingsRepository
from app.services.automatic_opportunity_service import AutomaticOpportunityService
from app.services.child_offer_service import (
    ChildOfferOperationConflict,
    ChildOfferOperationPending,
    ChildOfferService,
)
from app.services.child_offers_client import ChildOffersClient, ChildOffersError


router = APIRouter(prefix="/profiles/{profile_id}/child-offers", tags=["child offers"])


def get_child_offers_client() -> ChildOffersClient:
    settings = get_settings()
    return ChildOffersClient(
        settings.analytics_service_url,
        settings.analytics_service_token.get_secret_value() if settings.analytics_service_token else "",
        settings.analytics_timeout_seconds,
    )


def _require_test_profile(profile: Profile) -> None:
    settings = get_settings()
    allowed = {item.strip() for item in settings.child_offers_test_profile_ids.split(",") if item.strip()}
    if (
        settings.environment != "development" or not settings.child_offers_test_enabled
        or str(profile.id) not in allowed or not profile.alias.startswith("TEST_CHILD_OFFERS_")
        or not profile.active
    ):
        raise HTTPException(status_code=404, detail="Child offers test is unavailable")


def _error(error: ChildOffersError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={"code": error.code},
        headers={"Cache-Control": "no-store"},
    )


def _automatic_error(outcome: str, code: str, status_code: int) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"outcome": outcome, "code": code},
        headers={"Cache-Control": "no-store"},
    )


@router.get("/settings", response_model=AutomaticOfferSettingsRead)
async def read_automatic_offer_settings(
    profile: Profile = Depends(require_verified_profile_access),
    db: AsyncSession = Depends(get_db),
):
    settings = await AutomaticOfferSettingsRepository(db).get(profile.id)
    if settings is None:
        raise HTTPException(status_code=404, detail="Automatic offer settings not found")
    return settings


@router.put("/settings", response_model=AutomaticOfferSettingsRead)
async def update_automatic_offer_settings(
    data: AutomaticOfferSettingsUpdate,
    profile: Profile = Depends(require_verified_profile_access),
    adult: AdultAccount = Depends(get_current_adult),
    db: AsyncSession = Depends(get_db),
):
    return await AutomaticOfferSettingsRepository(db).upsert(
        profile_id=profile.id,
        adult=adult,
        automatic_enabled=data.automatic_enabled,
        shared_phone=data.shared_phone,
    )


@router.post(
    "/opportunities",
    response_model=AutomaticOpportunityRead,
    response_model_exclude_none=True,
)
async def evaluate_automatic_opportunity(
    data: AutomaticOpportunityCreate,
    response: Response,
    device: Device = Depends(require_usage_monitoring_authorization),
    db: AsyncSession = Depends(get_db),
    analytics: ChildOffersClient = Depends(get_child_offers_client),
):
    response.headers["Cache-Control"] = "no-store"
    if not get_settings().adaptive_child_offers_enabled:
        raise _automatic_error("retryable_error", "ADAPTIVE_CHILD_OFFERS_DISABLED", 503)
    try:
        return await AutomaticOpportunityService(
            profile_settings=ProfileSettingsRepository(db),
            offer_settings=AutomaticOfferSettingsRepository(db),
            preferences=ProfileActivityPreferencesRepository(db),
            parental_responses=ExperimentalPmumRepository(db),
            activity_offers=ActivityOfferRepository(db),
            child_offers=ChildOfferService(db, analytics),
        ).evaluate(device.profile, device, data)
    except ChildOfferOperationConflict:
        raise _automatic_error("definitive_error", "OPPORTUNITY_CONFLICT", 409) from None
    except ChildOfferOperationPending:
        raise _automatic_error("retryable_error", "OPPORTUNITY_PENDING", 409) from None
    except ChildOffersError as error:
        outcome = (
            "retryable_error"
            if error.status_code in (503, 504)
            else "definitive_error"
        )
        raise _automatic_error(outcome, error.code, error.status_code) from None
    except ValueError:
        raise _automatic_error("definitive_error", "INVALID_COORDINATOR_OFFER", 502) from None


@router.post(
    "/opportunities/events",
    response_model=ChildOfferEventReceipt,
    response_model_exclude_unset=True,
)
async def record_automatic_offer_event(
    data: ChildOfferEvent,
    response: Response,
    device: Device = Depends(require_usage_monitoring_authorization),
    db: AsyncSession = Depends(get_db),
    analytics: ChildOffersClient = Depends(get_child_offers_client),
):
    response.headers["Cache-Control"] = "no-store"
    if not get_settings().adaptive_child_offers_enabled:
        raise _automatic_error("retryable_error", "ADAPTIVE_CHILD_OFFERS_DISABLED", 503)
    if data.profile_id != str(device.profile_id):
        raise _automatic_error("definitive_error", "PROFILE_MISMATCH", 422)
    offers = ActivityOfferRepository(db)
    try:
        local_offer_id = uuid.UUID(data.offer_id)
        local_offer = await offers.get_offer(local_offer_id)
    except ValueError:
        local_offer = None
    if (
        local_offer is None
        or local_offer.profile_id != device.profile_id
        or local_offer.device_id != device.id
        or local_offer.context != "automatic"
    ):
        raise _automatic_error("definitive_error", "AUTOMATIC_OFFER_NOT_FOUND", 404)
    if data.event == "selected" and not await offers.has_recorded_selection(
        offer_id=local_offer_id,
        profile_id=device.profile_id,
        device_id=device.id,
        activity_id=data.selected_activity_id,
    ):
        raise _automatic_error("definitive_error", "SELECTION_NOT_RECORDED", 409)
    try:
        return await ChildOfferService(db, analytics).event(
            device.profile_id,
            device.id,
            data.model_dump(mode="json", exclude_none=True),
        )
    except ChildOfferOperationConflict:
        raise _automatic_error("definitive_error", "OPPORTUNITY_EVENT_CONFLICT", 409) from None
    except ChildOfferOperationPending:
        raise _automatic_error("retryable_error", "OPPORTUNITY_PENDING", 409) from None
    except ChildOffersError as error:
        outcome = (
            "retryable_error"
            if error.status_code in (503, 504)
            else "definitive_error"
        )
        raise _automatic_error(outcome, error.code, error.status_code) from None


@router.post("/evaluate", response_model=ChildOfferEvaluationReceipt, response_model_exclude_unset=True)
async def evaluate_child_offer(
    data: ChildOfferEvaluation,
    response: Response,
    profile: Profile = Depends(require_device_profile_access),
    device: Device = Depends(get_current_device),
    db: AsyncSession = Depends(get_db),
    analytics: ChildOffersClient = Depends(get_child_offers_client),
):
    _require_test_profile(profile)
    scope = str(profile.id)
    evidence = data.evidence
    if (
        evidence.profile_id != scope
        or not evidence.series_id.startswith(scope + ".")
        or not evidence.interval_id.startswith(scope + ".")
    ):
        raise HTTPException(status_code=422, detail="Evidence identity does not match profile")
    response.headers["Cache-Control"] = "no-store"
    try:
        return await ChildOfferService(db, analytics).evaluate(
            profile.id, device.id, data.model_dump(mode="json", exclude_none=True),
        )
    except ChildOfferOperationConflict:
        raise HTTPException(status_code=409, detail="Child offer operation conflict") from None
    except ChildOfferOperationPending:
        raise HTTPException(status_code=409, detail="Retry the pending child offer operation") from None
    except ChildOffersError as error:
        raise _error(error) from None


@router.post("/events", response_model=ChildOfferEventReceipt, response_model_exclude_unset=True)
async def record_child_offer_event(
    data: ChildOfferEvent,
    response: Response,
    profile: Profile = Depends(require_device_profile_access),
    device: Device = Depends(get_current_device),
    db: AsyncSession = Depends(get_db),
    analytics: ChildOffersClient = Depends(get_child_offers_client),
):
    _require_test_profile(profile)
    if data.profile_id != str(profile.id):
        raise HTTPException(status_code=422, detail="Event identity does not match profile")
    response.headers["Cache-Control"] = "no-store"
    try:
        return await ChildOfferService(db, analytics).event(
            profile.id, device.id, data.model_dump(mode="json", exclude_none=True),
        )
    except ChildOfferOperationConflict:
        raise HTTPException(status_code=409, detail="Child offer operation conflict") from None
    except ChildOfferOperationPending:
        raise HTTPException(status_code=409, detail="Retry the pending child offer operation") from None
    except ChildOffersError as error:
        raise _error(error) from None


@router.get("/history", response_model=ChildOfferHistory, response_model_exclude_unset=True)
async def read_child_offer_history(
    response: Response,
    profile: Profile = Depends(require_verified_profile_access),
    analytics: ChildOffersClient = Depends(get_child_offers_client),
):
    _require_test_profile(profile)
    response.headers["Cache-Control"] = "no-store"
    try:
        return await analytics.history(str(profile.id))
    except ChildOffersError as error:
        raise _error(error) from None
