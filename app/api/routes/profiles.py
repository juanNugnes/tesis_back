import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_profile
from app.db.models import Profile
from app.db.session import get_db
from app.repositories.profile_repository import ProfileRepository
from app.schemas.profile import ProfileCreate, ProfileDetailRead, ProfileRead, ProfileUpdate
from app.services.profile_service import ProfileService

router = APIRouter(prefix="/profiles", tags=["profiles"])


@router.post("", response_model=ProfileDetailRead, status_code=status.HTTP_201_CREATED)
async def create_profile(data: ProfileCreate, db: AsyncSession = Depends(get_db)):
    profile = await ProfileRepository(db).create(data)
    return ProfileService.detail(profile)


@router.get("/{profile_id}", response_model=ProfileDetailRead)
async def read_profile(profile: Profile = Depends(get_profile)):
    return ProfileService.detail(profile)


@router.put("/{profile_id}", response_model=ProfileRead)
async def update_profile(data: ProfileUpdate, profile: Profile = Depends(get_profile), db: AsyncSession = Depends(get_db)):
    return await ProfileRepository(db).update(profile, data)
