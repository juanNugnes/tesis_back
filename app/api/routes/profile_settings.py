from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_profile
from app.db.models import Profile
from app.db.session import get_db
from app.repositories.profile_settings_repository import ProfileSettingsRepository
from app.schemas.profile_settings import ProfileSettingsRead, ProfileSettingsUpdate
from app.services.profile_settings_service import ProfileSettingsService

router = APIRouter(prefix="/profiles/{profile_id}/settings", tags=["profile settings"])


@router.get("", response_model=ProfileSettingsRead)
async def read_profile_settings(profile: Profile = Depends(get_profile), db: AsyncSession = Depends(get_db)):
    return await ProfileSettingsService(ProfileSettingsRepository(db)).get(profile.id)


@router.put("", response_model=ProfileSettingsRead)
async def update_profile_settings(data: ProfileSettingsUpdate, profile: Profile = Depends(get_profile), db: AsyncSession = Depends(get_db)):
    return await ProfileSettingsService(ProfileSettingsRepository(db)).upsert(profile.id, data)
