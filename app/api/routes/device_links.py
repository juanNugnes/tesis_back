import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_profile, require_profile_access
from app.core.config import get_settings
from app.db.models import Profile
from app.db.session import get_db
from app.schemas.device import DeviceCreate, DeviceRead
from app.schemas.profile_link import (
    AdultProfileLinkCodeRead,
    DeviceLinkClaimRequest,
    DeviceLinkClaimResponse,
    DeviceLinkCodeRead,
)
from app.services.device_service import (
    DeviceAlreadyLinkedToAnotherProfileError,
    DeviceService,
    InactiveProfileError,
)
from app.services.profile_link_service import (
    InvalidLinkCodeError,
    LinkCodeGenerationError,
    ProfileLinkService,
)
from app.services.profile_service import ProfileService

router = APIRouter(tags=["device links"])


def link_service(db: AsyncSession) -> ProfileLinkService:
    settings = get_settings()
    return ProfileLinkService(
        db,
        settings.device_link_code_hash_secret.get_secret_value(),
        settings.device_link_code_ttl_minutes,
    )


@router.post(
    "/profiles/{profile_id}/devices",
    response_model=DeviceRead,
    status_code=status.HTTP_201_CREATED,
)
async def register_device(
    data: DeviceCreate,
    response: Response,
    profile: Profile = Depends(get_profile),
    db: AsyncSession = Depends(get_db),
):
    try:
        registration = await DeviceService(db).register(profile, data)
    except DeviceAlreadyLinkedToAnotherProfileError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Installation is already linked to another profile",
        )
    except InactiveProfileError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Profile is inactive")

    if not registration.created:
        response.status_code = status.HTTP_200_OK
    return registration.device


@router.post(
    "/profiles/{profile_id}/device-link-codes",
    response_model=DeviceLinkCodeRead,
    status_code=status.HTTP_201_CREATED,
    description="Requires an adult Bearer token with an active link to the requested Profile.",
)
async def create_device_link_code(
    profile: Profile = Depends(require_profile_access),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await link_service(db).generate(profile)
    except InactiveProfileError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Profile is inactive")
    except LinkCodeGenerationError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to generate link code",
        )


@router.post(
    "/profiles/{profile_id}/adult-link-codes",
    response_model=AdultProfileLinkCodeRead,
    status_code=status.HTTP_201_CREATED,
    description=(
        "Bootstrap provisional from the child side. DEVICE AUTH REQUIRED BEFORE PRODUCTION; "
        "no verifiable Device credential exists in this stage."
    ),
)
async def create_adult_profile_link_code(
    profile: Profile = Depends(get_profile),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await link_service(db).generate_adult_profile_link_code(profile)
    except InactiveProfileError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Profile is inactive")
    except LinkCodeGenerationError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to generate link code",
        )


@router.post(
    "/device-links/claim",
    response_model=DeviceLinkClaimResponse,
    description="Claims a single-use DEVICE_LINK code. This MVP flow is not full authentication.",
)
async def claim_device_link(
    data: DeviceLinkClaimRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        device, profile = await link_service(db).claim(data)
    except InvalidLinkCodeError:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Invalid or expired link code",
        )
    except DeviceAlreadyLinkedToAnotherProfileError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Installation is already linked to another profile",
        )

    return DeviceLinkClaimResponse(
        device=DeviceRead.model_validate(device),
        profile=ProfileService.detail(profile),
    )
