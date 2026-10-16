from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_profile_access, require_usage_monitoring_authorization
from app.db.models import Device, Profile
from app.db.session import get_db
from app.repositories.usage_repository import UsageRepository
from app.schemas.usage import DailyUsageCreate, DailyUsageRead, UsageWriteResult
from app.services.usage_service import UsageConflictError, UsageService

router = APIRouter(prefix="/profiles/{profile_id}/usage", tags=["usage"])


@router.post(
    "",
    response_model=UsageWriteResult,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {
            "model": UsageWriteResult,
            "description": "A newer snapshot was applied or a known snapshot was unchanged.",
        },
        status.HTTP_409_CONFLICT: {
            "description": "The snapshot id or ordering conflicts with persisted snapshots.",
        },
    },
)
async def create_usage(
    data: DailyUsageCreate,
    response: Response,
    device: Device = Depends(require_usage_monitoring_authorization),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await UsageService(UsageRepository(db)).ingest(device.profile_id, device.id, data)
        response.status_code = (
            status.HTTP_201_CREATED if result.result == "created" else status.HTTP_200_OK
        )
        response.headers["X-Usage-Write-Result"] = result.result
        return result
    except UsageConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": exc.detail},
        )


@router.get("", response_model=list[DailyUsageRead])
async def list_usage(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    profile: Profile = Depends(require_profile_access),
    db: AsyncSession = Depends(get_db),
):
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must not be after date_to")
    return await UsageService(UsageRepository(db)).list(
        profile.id,
        date_from=date_from,
        date_to=date_to,
    )


@router.get("/{day}", response_model=DailyUsageRead)
async def read_usage(day: date, profile: Profile = Depends(require_profile_access), db: AsyncSession = Depends(get_db)):
    try:
        return await UsageService(UsageRepository(db)).get(profile.id, day)
    except LookupError:
        raise HTTPException(status_code=404, detail="Usage not found")

