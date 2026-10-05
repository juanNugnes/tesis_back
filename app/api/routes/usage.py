import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_profile, require_profile_access
from app.db.models import Profile
from app.db.session import get_db
from app.repositories.usage_repository import UsageRepository
from app.schemas.usage import DailyUsageCreate, DailyUsageRead
from app.services.usage_service import DuplicateUsageError, UsageService

router = APIRouter(prefix="/profiles/{profile_id}/usage", tags=["usage"])


@router.post("", response_model=DailyUsageRead, status_code=status.HTTP_201_CREATED)
async def create_usage(data: DailyUsageCreate, profile: Profile = Depends(get_profile), db: AsyncSession = Depends(get_db)):
    try:
        return await UsageService(UsageRepository(db)).create(profile.id, data)
    except DuplicateUsageError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Usage already exists for this profile and date")


@router.get("", response_model=list[DailyUsageRead])
async def list_usage(profile: Profile = Depends(require_profile_access), db: AsyncSession = Depends(get_db)):
    return await UsageRepository(db).list(profile.id)


@router.get("/{day}", response_model=DailyUsageRead)
async def read_usage(day: date, profile: Profile = Depends(require_profile_access), db: AsyncSession = Depends(get_db)):
    usage = await UsageRepository(db).get_by_date(profile.id, day)
    if usage is None: raise HTTPException(status_code=404, detail="Usage not found")
    return usage

