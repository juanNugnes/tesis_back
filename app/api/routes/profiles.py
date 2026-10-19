import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    get_device_bootstrap_service,
    require_profile_principal_access,
)
from app.api.link_rate_limits import enforce_link_rate
from app.core.config import get_settings
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
    request: Request,
    service: DeviceBootstrapService = Depends(get_device_bootstrap_service),
    db: AsyncSession = Depends(get_db),
):
    await enforce_link_rate(
        db, request, "profile_create_ip", get_settings().profile_create_ip_limit
    )
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
