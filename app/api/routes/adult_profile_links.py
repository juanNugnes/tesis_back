from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_adult
from app.core.config import get_settings
from app.db.models import AdultAccount
from app.db.session import get_db
from app.schemas.profile_link import (
    AdultProfileLinkClaimRequest,
    AdultProfileLinkClaimResponse,
    AdultProfileLinkRead,
)
from app.services.profile_link_service import (
    InvalidLinkCodeError,
    ProfileLinkService,
    RevokedAdultProfileLinkError,
)
from app.services.profile_service import ProfileService

router = APIRouter(prefix="/adult/profile-links", tags=["adult profile links"])


def link_service(db: AsyncSession) -> ProfileLinkService:
    settings = get_settings()
    return ProfileLinkService(
        db,
        settings.device_link_code_hash_secret.get_secret_value(),
        settings.device_link_code_ttl_minutes,
        auto_approve_representative_verification=(
            settings.auto_approve_representative_verification
        ),
    )


@router.post("/claim", response_model=AdultProfileLinkClaimResponse)
async def claim_adult_profile_link(
    data: AdultProfileLinkClaimRequest,
    current_adult: AdultAccount = Depends(get_current_adult),
    db: AsyncSession = Depends(get_db),
):
    try:
        adult_link, profile = await link_service(db).claim_adult_profile_link(
            current_adult,
            data,
        )
    except InvalidLinkCodeError:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Invalid or expired link code",
        )
    except RevokedAdultProfileLinkError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Profile link cannot be reactivated automatically",
        )

    return AdultProfileLinkClaimResponse(
        profile=ProfileService.detail(profile),
        link=AdultProfileLinkRead.model_validate(adult_link),
    )
