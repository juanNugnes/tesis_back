import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import Avatar
from app.schemas.profile_settings import ProfileSettingsRead, ProfileSettingsUpdate


class ProfileCreate(BaseModel):
    alias: str = Field(min_length=1, max_length=100)
    age: int = Field(ge=6, le=12)
    avatar: Avatar
    active: bool = True
    settings: ProfileSettingsUpdate | None = None


class ProfileUpdate(BaseModel):
    alias: str | None = Field(default=None, min_length=1, max_length=100)
    age: int | None = Field(default=None, ge=6, le=12)
    avatar: Avatar | None = None
    active: bool | None = None


class ProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    alias: str
    age: int
    avatar: Avatar
    active: bool
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class ProfileDetailRead(ProfileRead):
    settings: ProfileSettingsRead
