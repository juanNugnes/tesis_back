from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_adult
from app.db.models import AdultAccount
from app.db.session import get_db
from app.schemas.profile import ProfileDetailRead
from app.services.adult_profile_service import AdultProfileService
from app.services.profile_service import ProfileService

router = APIRouter(prefix="/adult/profiles", tags=["adult profiles"])


@router.get("", response_model=list[ProfileDetailRead])
async def list_current_adult_profiles(
    current_adult: AdultAccount = Depends(get_current_adult),
    db: AsyncSession = Depends(get_db),
):
    profiles = await AdultProfileService(db).list_profiles(current_adult)
    return [ProfileService.detail(profile) for profile in profiles]
