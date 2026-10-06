from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_profile_principal_access
from app.db.models import Profile
from app.db.session import get_db
from app.repositories.profile_activity_preferences_repository import ProfileActivityPreferencesRepository
from app.schemas.profile_activity_preferences import (
    ProfileActivityPreferencesRead,
    ProfileActivityPreferencesUpdate,
)
from app.services.profile_activity_preferences_service import ProfileActivityPreferencesService

router = APIRouter(
    prefix="/profiles/{profile_id}/activity-preferences",
    tags=["profile activity preferences"],
)


@router.get("", response_model=ProfileActivityPreferencesRead)
async def read_profile_activity_preferences(
    profile: Profile = Depends(require_profile_principal_access),
    db: AsyncSession = Depends(get_db),
):
    service = ProfileActivityPreferencesService(ProfileActivityPreferencesRepository(db))
    return await service.get(profile.id)


@router.put("", response_model=ProfileActivityPreferencesRead)
async def update_profile_activity_preferences(
    data: ProfileActivityPreferencesUpdate,
    profile: Profile = Depends(require_profile_principal_access),
    db: AsyncSession = Depends(get_db),
):
    service = ProfileActivityPreferencesService(ProfileActivityPreferencesRepository(db))
    return await service.upsert(profile.id, data)
