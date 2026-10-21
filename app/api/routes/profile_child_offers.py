"""Explicitly gated, fictitious-profile integration with the real coordinator."""

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_device, require_device_profile_access, require_verified_profile_access
from app.core.config import get_settings
from app.db.models import Device, Profile
from app.db.session import get_db
from app.schemas.child_offer import (
    ChildOfferEvaluation, ChildOfferEvaluationReceipt, ChildOfferEvent,
    ChildOfferEventReceipt, ChildOfferHistory,
)
from app.services.child_offer_service import ChildOfferOperationConflict, ChildOfferOperationPending, ChildOfferService
from app.services.child_offers_client import ChildOffersClient, ChildOffersError


router = APIRouter(prefix="/profiles/{profile_id}/child-offers", tags=["child offers test"])


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
