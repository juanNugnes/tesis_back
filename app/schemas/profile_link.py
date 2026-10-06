import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.device import DeviceCreate, DeviceRead
from app.schemas.profile import ProfileDetailRead


class DeviceLinkCodeRead(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")
    expires_at: datetime
    purpose: Literal["DEVICE_LINK"]


class AdultProfileLinkCodeRead(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")
    expires_at: datetime
    purpose: Literal["ADULT_PROFILE_LINK"]


class DeviceLinkClaimRequest(DeviceCreate):
    code: str = Field(pattern=r"^\d{6}$")


class DeviceLinkClaimResponse(BaseModel):
    device: DeviceRead
    profile: ProfileDetailRead
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class AdultProfileLinkClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^\d{6}$")


class AdultProfileLinkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    profile_id: uuid.UUID
    active: bool
    created_at: datetime


class AdultProfileLinkClaimResponse(BaseModel):
    profile: ProfileDetailRead
    link: AdultProfileLinkRead
