import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import InvalidAccessTokenError
from app.db.models import AdultAccount, Profile
from app.db.session import get_db
from app.repositories.profile_repository import ProfileRepository
from app.services.adult_auth_service import AdultAuthService
from app.services.adult_profile_service import AdultProfileService
from app.services.otp_delivery_service import DevelopmentOtpDeliveryService, OtpDeliveryService

adult_bearer = HTTPBearer(auto_error=False, scheme_name="AdultBearer")


async def get_profile(profile_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> Profile:
    profile = await ProfileRepository(db).get(profile_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    return profile


def get_otp_delivery_service() -> OtpDeliveryService:
    return DevelopmentOtpDeliveryService()


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
        expose_dev_otp=settings.environment == "development" and settings.expose_dev_otp,
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


async def require_profile_access(
    profile_id: uuid.UUID,
    current_adult: AdultAccount = Depends(get_current_adult),
    db: AsyncSession = Depends(get_db),
) -> Profile:
    profile = await AdultProfileService(db).get_authorized_profile(current_adult, profile_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Profile access denied")
    return profile

