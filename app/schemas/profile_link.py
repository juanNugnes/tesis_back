from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.device import DeviceCreate, DeviceRead
from app.schemas.profile import ProfileDetailRead


class DeviceLinkCodeRead(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")
    expires_at: datetime
    purpose: Literal["DEVICE_LINK"]


class DeviceLinkClaimRequest(DeviceCreate):
    code: str = Field(pattern=r"^\d{6}$")


class DeviceLinkClaimResponse(BaseModel):
    device: DeviceRead
    profile: ProfileDetailRead
