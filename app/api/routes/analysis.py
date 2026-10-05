from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_profile_access
from app.db.models import Profile
from app.db.session import get_db
from app.repositories.analysis_repository import AnalysisRepository
from app.repositories.usage_repository import UsageRepository
from app.schemas.analysis import AnalysisCreate, AnalysisRead
from app.services.analysis_service import AnalysisService

router = APIRouter(prefix="/profiles/{profile_id}/analysis", tags=["analysis"])


@router.post("", response_model=AnalysisRead, status_code=status.HTTP_201_CREATED)
async def create_analysis(data: AnalysisCreate, profile: Profile = Depends(require_profile_access), db: AsyncSession = Depends(get_db)):
    usage = await UsageRepository(db).get_by_date(profile.id, data.date)
    if usage is None: raise HTTPException(status_code=404, detail="Usage not found for requested date")
    return await AnalysisService(AnalysisRepository(db)).analyze(usage)


@router.get("", response_model=list[AnalysisRead])
async def list_analysis(profile: Profile = Depends(require_profile_access), db: AsyncSession = Depends(get_db)):
    return await AnalysisRepository(db).list(profile.id)


@router.get("/latest", response_model=AnalysisRead)
async def latest_analysis(profile: Profile = Depends(require_profile_access), db: AsyncSession = Depends(get_db)):
    result = await AnalysisRepository(db).latest(profile.id)
    if result is None: raise HTTPException(status_code=404, detail="No analysis found")
    return result

