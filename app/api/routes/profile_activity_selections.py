from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_device, require_device_profile_access
from app.db.models import Device, Profile
from app.db.session import get_db
from app.repositories.activity_offer_repository import ActivityOfferRepository
from app.schemas.activity_selection import (
    ActivitySelectionCountRead,
    ActivitySelectionCreate,
    ActivitySelectionRead,
)
from app.services.activity_selection_service import (
    ActivityNotOfferedError,
    ActivityOfferAccessDeniedError,
    ActivityOfferNotFoundError,
    ActivitySelectionConflictError,
    ActivitySelectionService,
)

router = APIRouter(
    prefix="/profiles/{profile_id}/activity-selections",
    tags=["profile activity selections"],
)


@router.get("/count", response_model=ActivitySelectionCountRead)
async def count_activity_selections(
    start_epoch_millis: Annotated[int, Query(ge=0)],
    end_epoch_millis: Annotated[int, Query(ge=0)],
    profile: Profile = Depends(require_device_profile_access),
    db: AsyncSession = Depends(get_db),
):
    if end_epoch_millis <= start_epoch_millis:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="end_epoch_millis must be greater than start_epoch_millis",
        )
    count = await ActivityOfferRepository(db).count_selections(
        profile_id=profile.id,
        start_epoch_millis=start_epoch_millis,
        end_epoch_millis=end_epoch_millis,
    )
    return ActivitySelectionCountRead(count=count)


@router.post("", response_model=ActivitySelectionRead)
async def create_activity_selection(
    data: ActivitySelectionCreate,
    profile: Profile = Depends(require_device_profile_access),
    current_device: Device = Depends(get_current_device),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await ActivitySelectionService(ActivityOfferRepository(db)).create(
            profile_id=profile.id,
            device_id=current_device.id,
            data=data,
        )
    except ActivityOfferNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Activity offer not found",
        )
    except ActivityOfferAccessDeniedError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Activity offer access denied",
        )
    except ActivityNotOfferedError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Activity was not part of the offer",
        )
    except ActivitySelectionConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Selection id was already used with different data",
        )
