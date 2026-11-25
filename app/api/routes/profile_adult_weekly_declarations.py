import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_adult, require_verified_profile_access
from app.db.models import AdultAccount, Profile
from app.db.session import get_db
from app.repositories.adult_weekly_declaration_repository import (
    AdultWeeklyDeclarationRepository,
)
from app.schemas.adult_weekly_declaration import (
    AdultWeeklyDeclarationCreate,
    AdultWeeklyDeclarationRead,
)
from app.services.adult_weekly_declaration_service import (
    AdultWeeklyDeclarationDateError,
    AdultWeeklyDeclarationDeviceError,
    AdultWeeklyDeclarationNotFoundError,
    AdultWeeklyDeclarationPersistenceError,
    AdultWeeklyDeclarationService,
)
from app.services.monitoring_authorization_service import (
    MonitoringAuthorizationService,
)


router = APIRouter(
    prefix="/profiles/{profile_id}/weekly-declarations",
    tags=["adult weekly declarations"],
)


def _service(db: AsyncSession) -> AdultWeeklyDeclarationService:
    return AdultWeeklyDeclarationService(AdultWeeklyDeclarationRepository(db))


async def _require_monitoring(profile_id: uuid.UUID, db: AsyncSession) -> None:
    if not await MonitoringAuthorizationService(db).is_usage_monitoring_allowed(
        profile_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Monitoring authorization is not active",
        )


@router.post(
    "",
    response_model=AdultWeeklyDeclarationRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_adult_weekly_declaration(
    data: AdultWeeklyDeclarationCreate,
    response: Response,
    profile: Profile = Depends(require_verified_profile_access),
    adult: AdultAccount = Depends(get_current_adult),
    db: AsyncSession = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"
    await _require_monitoring(profile.id, db)
    try:
        return await _service(db).create(
            profile_id=profile.id,
            adult=adult,
            data=data,
        )
    except AdultWeeklyDeclarationDeviceError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Device is not active for this profile",
        ) from None
    except AdultWeeklyDeclarationDateError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from None
    except AdultWeeklyDeclarationNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from None
    except AdultWeeklyDeclarationPersistenceError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Declaration could not be stored",
        ) from None


@router.get("", response_model=list[AdultWeeklyDeclarationRead])
async def list_adult_weekly_declarations(
    response: Response,
    profile: Profile = Depends(require_verified_profile_access),
    db: AsyncSession = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"
    await _require_monitoring(profile.id, db)
    return await _service(db).list(profile_id=profile.id)


@router.delete(
    "/{declaration_id}",
    response_model=AdultWeeklyDeclarationRead,
)
async def revoke_adult_weekly_declaration(
    declaration_id: uuid.UUID,
    response: Response,
    profile: Profile = Depends(require_verified_profile_access),
    adult: AdultAccount = Depends(get_current_adult),
    db: AsyncSession = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"
    await _require_monitoring(profile.id, db)
    try:
        return await _service(db).revoke(
            profile_id=profile.id,
            declaration_id=declaration_id,
            adult=adult,
        )
    except AdultWeeklyDeclarationNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Declaration not found",
        ) from None
    except AdultWeeklyDeclarationPersistenceError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Declaration could not be revoked",
        ) from None
