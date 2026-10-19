import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import (
    ADULT_ACCESS_TOKEN_TYPE,
    DEVICE_ACCESS_TOKEN_TYPE,
    InvalidAccessTokenError,
    peek_access_token_type,
)
from app.db.models import (
    AdultAccount,
    Device,
    Profile,
    RepresentativeVerificationStatus,
)
from app.db.session import get_db
from app.repositories.profile_repository import ProfileRepository
from app.repositories.representative_verification_repository import (
    RepresentativeVerificationRepository,
)
from app.services.adult_auth_service import AdultAuthService
from app.services.adult_profile_service import AdultProfileService
from app.services.device_auth_service import DeviceAuthService
from app.services.device_bootstrap_service import DeviceBootstrapService
from app.services.monitoring_authorization_service import MonitoringAuthorizationService
from app.services.otp_delivery_service import (
    DevelopmentOtpDeliveryService,
    OtpDeliveryService,
    SesOtpDeliveryService,
)
from app.services.otp_rate_limiter import OtpRateLimiter

adult_bearer = HTTPBearer(auto_error=False, scheme_name="AdultBearer")
device_bearer = HTTPBearer(auto_error=False, scheme_name="DeviceBearer")
device_bootstrap_bearer = HTTPBearer(auto_error=False, scheme_name="DeviceBootstrapBearer")


async def get_profile(profile_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> Profile:
    profile = await ProfileRepository(db).get(profile_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    return profile


def get_otp_delivery_service() -> OtpDeliveryService:
    settings = get_settings()
    if settings.otp_email_provider == "ses":
        if not settings.ses_sender_email:
            raise HTTPException(status_code=503, detail="OTP delivery is not configured")
        try:
            return SesOtpDeliveryService(
                settings.ses_sender_email, settings.ses_region, settings.ses_timeout_seconds
            )
        except Exception:
            raise HTTPException(status_code=503, detail="OTP delivery is unavailable") from None
    if settings.otp_email_provider == "development" and settings.environment == "development":
        return DevelopmentOtpDeliveryService()
    raise HTTPException(status_code=503, detail="OTP delivery is not configured")


async def get_adult_auth_service(
    db: AsyncSession = Depends(get_db),
    delivery: OtpDeliveryService = Depends(get_otp_delivery_service),
) -> AdultAuthService:
    settings = get_settings()
    if settings.adult_auth_jwt_secret is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Adult authentication is not configured",
        )
    secret = settings.adult_auth_jwt_secret.get_secret_value()
    if len(secret) < 32:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Adult authentication is not configured",
        )
    return AdultAuthService(
        db,
        secret,
        settings.adult_auth_jwt_exp_minutes,
        settings.adult_otp_ttl_minutes,
        settings.adult_otp_max_attempts,
        delivery,
        OtpRateLimiter(db, secret, settings),
        expose_dev_otp=(
            settings.environment == "development"
            and settings.otp_email_provider == "development"
            and settings.expose_dev_otp
        ),
    )


async def get_current_adult(
    credentials: HTTPAuthorizationCredentials | None = Depends(adult_bearer),
    db: AsyncSession = Depends(get_db),
    delivery: OtpDeliveryService = Depends(get_otp_delivery_service),
) -> AdultAccount:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    service = await get_adult_auth_service(db, delivery)
    try:
        return await service.authenticate(credentials.credentials)
    except InvalidAccessTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def build_device_auth_service(db: AsyncSession) -> DeviceAuthService:
    settings = get_settings()
    if settings.device_auth_jwt_secret is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Device authentication is not configured",
        )
    secret = settings.device_auth_jwt_secret.get_secret_value()
    if len(secret) < 32:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Device authentication is not configured",
        )
    return DeviceAuthService(db, secret, settings.device_auth_jwt_exp_minutes)


async def get_device_auth_service(db: AsyncSession = Depends(get_db)) -> DeviceAuthService:
    return build_device_auth_service(db)


async def get_device_bootstrap_service(
    db: AsyncSession = Depends(get_db),
    device_auth: DeviceAuthService = Depends(get_device_auth_service),
) -> DeviceBootstrapService:
    settings = get_settings()
    secret = settings.device_auth_jwt_secret
    if secret is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Device authentication is not configured",
        )
    return DeviceBootstrapService(
        db,
        secret.get_secret_value(),
        settings.device_bootstrap_ttl_minutes,
        device_auth,
    )


async def get_current_device(
    credentials: HTTPAuthorizationCredentials | None = Depends(device_bearer),
    service: DeviceAuthService = Depends(get_device_auth_service),
) -> Device:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return await service.authenticate(credentials.credentials)
    except InvalidAccessTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired device access token",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def require_device_profile_access(
    profile_id: uuid.UUID,
    current_device: Device = Depends(get_current_device),
) -> Profile:
    if current_device.profile_id != profile_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Profile access denied")
    return current_device.profile


async def require_usage_monitoring_authorization(
    profile_id: uuid.UUID,
    current_device: Device = Depends(get_current_device),
    db: AsyncSession = Depends(get_db),
) -> Device:
    if current_device.profile_id != profile_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Profile access denied")
    allowed = await MonitoringAuthorizationService(db).is_usage_monitoring_allowed(profile_id)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Monitoring authorization is not active",
        )
    return current_device


async def require_profile_principal_access(
    profile_id: uuid.UUID,
    adult_credentials: HTTPAuthorizationCredentials | None = Depends(adult_bearer),
    device_credentials: HTTPAuthorizationCredentials | None = Depends(device_bearer),
    db: AsyncSession = Depends(get_db),
    delivery: OtpDeliveryService = Depends(get_otp_delivery_service),
) -> Profile:
    credentials = adult_credentials or device_credentials
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    token_type = peek_access_token_type(token)
    try:
        if token_type == ADULT_ACCESS_TOKEN_TYPE:
            adult_service = await get_adult_auth_service(db, delivery)
            adult = await adult_service.authenticate(token)
            profile = await AdultProfileService(db).get_authorized_profile(adult, profile_id)
            if profile is None:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Profile access denied",
                )
            return profile

        if token_type == DEVICE_ACCESS_TOKEN_TYPE:
            device = await build_device_auth_service(db).authenticate(token)
            if device.profile_id != profile_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Profile access denied",
                )
            return device.profile
    except InvalidAccessTokenError:
        pass

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired access token",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_profile_access(
    profile_id: uuid.UUID,
    current_adult: AdultAccount = Depends(get_current_adult),
    db: AsyncSession = Depends(get_db),
) -> Profile:
    profile = await AdultProfileService(db).get_authorized_profile(current_adult, profile_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Profile access denied")
    return profile


async def require_verified_profile_access(
    profile: Profile = Depends(require_profile_access),
    current_adult: AdultAccount = Depends(get_current_adult),
    db: AsyncSession = Depends(get_db),
) -> Profile:
    verification = await RepresentativeVerificationRepository(db).get_by_adult_profile(
        current_adult.id,
        profile.id,
    )
    if (
        not profile.active
        or verification is None
        or verification.status != RepresentativeVerificationStatus.APPROVED
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Verified profile access required",
        )
    return profile

