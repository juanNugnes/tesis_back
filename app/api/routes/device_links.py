import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    device_bootstrap_bearer,
    get_device_auth_service,
    get_device_bootstrap_service,
    require_device_profile_access,
    require_profile_access,
)
from app.api.link_rate_limits import enforce_link_rate
from app.core.config import get_settings
from app.db.models import Profile
from app.db.session import get_db
from app.schemas.device import DeviceCreate, DeviceRead, DeviceRegistrationRead
from app.schemas.profile_link import (
    AdultProfileLinkCodeRead,
    DeviceLinkClaimRequest,
    DeviceLinkClaimResponse,
    DeviceLinkCodeRead,
)
from app.services.device_service import (
    DeviceAlreadyLinkedToAnotherProfileError,
    InactiveProfileError,
)
from app.services.device_auth_service import DeviceAuthService
from app.services.device_bootstrap_service import (
    BootstrapProfileMismatchError,
    DeviceBootstrapService,
    InvalidBootstrapTokenError,
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
    response_model=DeviceRegistrationRead,
    status_code=status.HTTP_201_CREATED,
    description="Registers the first Device using a one-time DeviceBootstrapBearer credential.",
)
async def register_device(
    profile_id: uuid.UUID,
    data: DeviceCreate,
    credentials: HTTPAuthorizationCredentials | None = Depends(device_bootstrap_bearer),
    service: DeviceBootstrapService = Depends(get_device_bootstrap_service),
):
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        result = await service.register_device(profile_id, credentials.credentials, data)
    except InvalidBootstrapTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired bootstrap token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except BootstrapProfileMismatchError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bootstrap token does not authorize this profile",
        )
    except DeviceAlreadyLinkedToAnotherProfileError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Installation is already linked to another profile",
        )
    except InactiveProfileError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Profile is inactive")

    return DeviceRegistrationRead(
        **DeviceRead.model_validate(result.device).model_dump(),
        access_token=result.token.access_token,
        expires_in=result.token.expires_in,
    )


@router.post(
    "/profiles/{profile_id}/device-link-codes",
    response_model=DeviceLinkCodeRead,
    status_code=status.HTTP_201_CREATED,
    description="Requires an adult Bearer token with an active link to the requested Profile.",
)
async def create_device_link_code(
    request: Request,
    profile: Profile = Depends(require_profile_access),
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()
    await enforce_link_rate(
        db, request, "device_code_ip", settings.link_code_issue_ip_limit,
        (("device_code_profile", str(profile.id), settings.link_code_issue_profile_limit),),
    )
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
        "Requires a Device Bearer token whose persisted Device belongs to the requested Profile."
    ),
)
async def create_adult_profile_link_code(
    request: Request,
    profile: Profile = Depends(require_device_profile_access),
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()
    await enforce_link_rate(
        db, request, "adult_code_ip", settings.link_code_issue_ip_limit,
        (("adult_code_profile", str(profile.id), settings.link_code_issue_profile_limit),),
    )
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
    request: Request,
    db: AsyncSession = Depends(get_db),
    device_auth: DeviceAuthService = Depends(get_device_auth_service),
):
    await enforce_link_rate(
        db, request, "device_claim_ip", get_settings().link_claim_ip_limit
    )
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

    token = device_auth.issue(device)
    return DeviceLinkClaimResponse(
        device=DeviceRead.model_validate(device),
        profile=ProfileService.detail(profile),
        access_token=token.access_token,
        expires_in=token.expires_in,
    )
