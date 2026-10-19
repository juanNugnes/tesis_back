from fastapi import APIRouter, Depends, HTTPException, Response

from app.api.dependencies import require_verified_profile_access
from app.core.config import get_settings
from app.db.models import Profile
from app.schemas.adult_message import AdultMessageHistory
from app.services.adult_messages_client import AdultMessagesClient, AdultMessagesError


router = APIRouter(
    prefix="/profiles/{profile_id}/adult-messages", tags=["adult messages"],
)


def get_adult_messages_client() -> AdultMessagesClient:
    settings = get_settings()
    return AdultMessagesClient(
        settings.analytics_service_url,
        settings.analytics_service_token.get_secret_value() if settings.analytics_service_token else "",
        settings.analytics_timeout_seconds,
    )


@router.get(
    "/history", response_model=AdultMessageHistory, response_model_exclude_unset=True,
)
async def read_adult_message_history(
    response: Response,
    profile: Profile = Depends(require_verified_profile_access),
    analytics: AdultMessagesClient = Depends(get_adult_messages_client),
):
    response.headers["Cache-Control"] = "no-store"
    try:
        return await analytics.history(profile.id)
    except AdultMessagesError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": str(error), "retryable": error.retryable},
            headers={"Cache-Control": "no-store"},
        ) from None
