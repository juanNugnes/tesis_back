from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies import get_adult_auth_service, get_current_adult
from app.core.client_ip import otp_client_ip
from app.core.config import get_settings
from app.db.models import AdultAccount
from app.schemas.adult_auth import (
    AdultAccessTokenRead,
    AdultOtpRequest,
    AdultOtpRequestResponse,
    AdultOtpVerifyRequest,
    AdultRead,
)
from app.services.adult_auth_service import AdultAuthService, InvalidOtpError
from app.services.otp_delivery_service import OtpDeliveryError
from app.services.otp_rate_limiter import OtpRateLimitExceeded

router = APIRouter(tags=["adult auth"])

OTP_ACCEPTED_MESSAGE = "If the address is valid, a verification code was generated."


@router.post(
    "/auth/adult/otp/request",
    response_model=AdultOtpRequestResponse,
    response_model_exclude_none=True,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_adult_otp(
    data: AdultOtpRequest,
    request: Request,
    service: AdultAuthService = Depends(get_adult_auth_service),
):
    try:
        client_ip = otp_client_ip(request, get_settings().otp_trusted_proxy_cidrs)
        result = await service.request_otp(data.email, client_ip)
    except OtpRateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many OTP attempts",
            headers={"Retry-After": str(exc.retry_after)},
        ) from None
    except OtpDeliveryError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Verification email temporarily unavailable",
        ) from None
    return AdultOtpRequestResponse(message=OTP_ACCEPTED_MESSAGE, dev_code=result.dev_code)


@router.post("/auth/adult/otp/verify", response_model=AdultAccessTokenRead)
async def verify_adult_otp(
    data: AdultOtpVerifyRequest,
    request: Request,
    service: AdultAuthService = Depends(get_adult_auth_service),
):
    try:
        client_ip = otp_client_ip(request, get_settings().otp_trusted_proxy_cidrs)
        result = await service.verify_otp(data.email, data.code, client_ip)
    except OtpRateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many OTP attempts",
            headers={"Retry-After": str(exc.retry_after)},
        ) from None
    except InvalidOtpError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired verification code",
        )
    return AdultAccessTokenRead(
        access_token=result.access_token,
        expires_in=result.expires_in,
    )


@router.get("/auth/adult/me", response_model=AdultRead)
async def read_current_adult(current_adult: AdultAccount = Depends(get_current_adult)):
    return current_adult
