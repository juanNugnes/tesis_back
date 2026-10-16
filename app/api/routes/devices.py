import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_adult, require_verified_profile_access
from app.db.models import AdultAccount, Profile
from app.db.session import get_db
from app.schemas.device import DeviceRead
from app.services.device_service import DeviceNotFoundError, DeviceService


router = APIRouter(prefix="/profiles/{profile_id}/devices", tags=["devices"])


@router.get("", response_model=list[DeviceRead])
async def list_devices(
    profile: Profile = Depends(require_verified_profile_access),
    db: AsyncSession = Depends(get_db),
):
    return await DeviceService(db).list_for_profile(profile.id)


@router.delete("/{device_id}", response_model=DeviceRead)
async def deactivate_device(
    device_id: uuid.UUID,
    profile: Profile = Depends(require_verified_profile_access),
    current_adult: AdultAccount = Depends(get_current_adult),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await DeviceService(db).deactivate(profile.id, device_id, current_adult)
    except DeviceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found",
        ) from None
