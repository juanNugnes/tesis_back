from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_adult_auth_service, get_current_adult
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
    service: AdultAuthService = Depends(get_adult_auth_service),
):
    try:
        result = await service.request_otp(data.email)
    except OtpDeliveryError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Verification email temporarily unavailable",
        ) from None
    return AdultOtpRequestResponse(message=OTP_ACCEPTED_MESSAGE, dev_code=result.dev_code)


@router.post("/auth/adult/otp/verify", response_model=AdultAccessTokenRead)
async def verify_adult_otp(
    data: AdultOtpVerifyRequest,
    service: AdultAuthService = Depends(get_adult_auth_service),
):
    try:
        result = await service.verify_otp(data.email, data.code)
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
