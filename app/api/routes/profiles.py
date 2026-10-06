import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    get_device_bootstrap_service,
    require_profile_principal_access,
)
from app.db.models import Profile
from app.db.session import get_db
from app.repositories.profile_repository import ProfileRepository
from app.schemas.profile import ProfileBootstrapRead, ProfileCreate, ProfileDetailRead, ProfileUpdate
from app.services.device_bootstrap_service import BootstrapGenerationError, DeviceBootstrapService
from app.services.profile_service import ProfileService

router = APIRouter(prefix="/profiles", tags=["profiles"])


@router.post("", response_model=ProfileBootstrapRead, status_code=status.HTTP_201_CREATED)
async def create_profile(
    data: ProfileCreate,
    service: DeviceBootstrapService = Depends(get_device_bootstrap_service),
):
    try:
        result = await service.create_profile(data)
    except BootstrapGenerationError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to generate device bootstrap token",
        )
    profile = ProfileService.detail(result.profile)
    return ProfileBootstrapRead(
        **profile.model_dump(),
        bootstrap_token=result.bootstrap_token,
        bootstrap_expires_at=result.expires_at,
    )


@router.get("/{profile_id}", response_model=ProfileDetailRead)
async def read_profile(profile: Profile = Depends(require_profile_principal_access)):
    return ProfileService.detail(profile)


@router.put("/{profile_id}", response_model=ProfileDetailRead)
async def update_profile(data: ProfileUpdate, profile: Profile = Depends(require_profile_principal_access), db: AsyncSession = Depends(get_db)):
    updated_profile = await ProfileRepository(db).update(profile, data)
    return ProfileService.detail(updated_profile)
