import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DeviceCreate(BaseModel):
    installation_id: str = Field(min_length=1, max_length=255)
    platform: Literal["ANDROID"]


class DeviceRead(DeviceCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    profile_id: uuid.UUID
    active: bool
    created_at: datetime
    updated_at: datetime
    last_seen_at: datetime | None


class DeviceRegistrationRead(DeviceRead):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
