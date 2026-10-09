from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_device_profile_access
from app.db.models import Profile
from app.db.session import get_db
from app.repositories.profile_activity_preferences_repository import (
    ProfileActivityPreferencesRepository,
)
from app.schemas.activity_suggestion import (
    ActivitySuggestionRequest,
    ActivitySuggestionsRead,
)
from app.services.activity_selector_gateway import (
    ActivitySelectorGateway,
    ActivitySelectorTechnicalError,
    UnavailableActivitySelectorGateway,
)
from app.services.activity_suggestion_service import ActivitySuggestionService

router = APIRouter(
    prefix="/profiles/{profile_id}/activity-suggestions",
    tags=["profile activity suggestions"],
)


def get_activity_selector_gateway() -> ActivitySelectorGateway:
    return UnavailableActivitySelectorGateway()


@router.post("", response_model=ActivitySuggestionsRead)
async def create_activity_suggestions(
    data: ActivitySuggestionRequest,
    profile: Profile = Depends(require_device_profile_access),
    db: AsyncSession = Depends(get_db),
    selector: ActivitySelectorGateway = Depends(get_activity_selector_gateway),
):
    try:
        return await ActivitySuggestionService(
            ProfileActivityPreferencesRepository(db),
            selector,
        ).suggest(profile.id, data.context)
    except ActivitySelectorTechnicalError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
