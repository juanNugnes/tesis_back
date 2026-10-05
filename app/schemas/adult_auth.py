import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


def normalize_email(value: str) -> str:
    normalized = value.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
        raise ValueError("Invalid email address")
    return normalized


class AdultOtpRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class AdultOtpRequestResponse(BaseModel):
    message: str
    dev_code: str | None = None


class AdultOtpVerifyRequest(AdultOtpRequest):
    code: str = Field(pattern=r"^\d{6}$")


class AdultAccessTokenRead(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class AdultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    active: bool
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None
