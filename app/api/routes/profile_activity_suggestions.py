from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_device, require_device_profile_access
from app.core.config import get_settings
from app.db.models import Device, Profile
from app.db.session import get_db
from app.repositories.profile_activity_preferences_repository import (
    ProfileActivityPreferencesRepository,
)
from app.repositories.activity_offer_repository import ActivityOfferRepository
from app.schemas.activity_suggestion import (
    ActivitySuggestionRequest,
    ActivitySuggestionsRead,
)
from app.services.activity_selector_gateway import (
    ActivitySelectorGateway,
    ActivitySelectorTechnicalError,
)
from app.services.activity_selector_http_gateway import HttpActivitySelectorGateway
from app.services.activity_suggestion_service import ActivitySuggestionService

router = APIRouter(
    prefix="/profiles/{profile_id}/activity-suggestions",
    tags=["profile activity suggestions"],
)


def get_activity_selector_gateway() -> ActivitySelectorGateway:
    settings = get_settings()
    token = (
        settings.analytics_service_token.get_secret_value()
        if settings.analytics_service_token is not None
        else ""
    )
    return HttpActivitySelectorGateway(
        settings.analytics_service_url,
        token,
        settings.analytics_timeout_seconds,
    )


@router.post("", response_model=ActivitySuggestionsRead)
async def create_activity_suggestions(
    data: ActivitySuggestionRequest,
    profile: Profile = Depends(require_device_profile_access),
    current_device: Device = Depends(get_current_device),
    db: AsyncSession = Depends(get_db),
    selector: ActivitySelectorGateway = Depends(get_activity_selector_gateway),
):
    try:
        return await ActivitySuggestionService(
            ProfileActivityPreferencesRepository(db),
            ActivityOfferRepository(db),
            selector,
        ).suggest(profile.id, current_device.id, data.context)
    except ActivitySelectorTechnicalError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
