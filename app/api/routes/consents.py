import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_adult, require_profile_access
from app.db.models import AdultAccount, Profile
from app.db.session import get_db
from app.schemas.consent import (
    ConsentGrantRequest,
    ConsentRead,
    RepresentativeVerificationRead,
)
from app.services.consent_service import (
    ActiveConsentConflictError,
    ConsentNotAllowedError,
    ConsentNotFoundError,
    ConsentService,
)
from app.services.representative_verification_service import (
    RepresentativeVerificationNotFoundError,
    RepresentativeVerificationService,
)

router = APIRouter(prefix="/profiles/{profile_id}", tags=["consent"])


@router.get(
    "/representative-verification",
    response_model=RepresentativeVerificationRead,
)
async def read_representative_verification(
    profile: Profile = Depends(require_profile_access),
    current_adult: AdultAccount = Depends(get_current_adult),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await RepresentativeVerificationService(db).get(
            current_adult.id,
            profile.id,
        )
    except RepresentativeVerificationNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Representative verification not found",
        )


@router.post(
    "/consents",
    response_model=ConsentRead,
    status_code=status.HTTP_201_CREATED,
)
async def grant_consent(
    data: ConsentGrantRequest,
    response: Response,
    profile: Profile = Depends(require_profile_access),
    current_adult: AdultAccount = Depends(get_current_adult),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await ConsentService(db).grant(
            current_adult,
            profile,
            privacy_notice_version=data.privacy_notice_version,
            purposes=data.purposes,
        )
    except ConsentNotAllowedError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Consent cannot be granted",
        )
    except ActiveConsentConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A different active consent already exists",
        )
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return ConsentRead.from_entity(result.consent)


@router.get("/consents/current", response_model=ConsentRead)
async def read_current_consent(
    profile: Profile = Depends(require_profile_access),
    current_adult: AdultAccount = Depends(get_current_adult),
    db: AsyncSession = Depends(get_db),
):
    try:
        consent = await ConsentService(db).get_current(current_adult, profile)
    except ConsentNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Consent not found")
    return ConsentRead.from_entity(consent)


@router.post("/consents/{consent_id}/revoke", response_model=ConsentRead)
async def revoke_consent(
    consent_id: uuid.UUID,
    profile: Profile = Depends(require_profile_access),
    current_adult: AdultAccount = Depends(get_current_adult),
    db: AsyncSession = Depends(get_db),
):
    try:
        consent = await ConsentService(db).revoke(current_adult, profile, consent_id)
    except ConsentNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Consent not found")
    return ConsentRead.from_entity(consent)
