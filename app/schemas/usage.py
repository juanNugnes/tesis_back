import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AppUsageCreate(BaseModel):
    package_name: str = Field(min_length=1, max_length=255)
    app_name: str | None = Field(default=None, max_length=150)
    category: str | None = Field(default=None, max_length=80)
    usage_minutes: int = Field(ge=0)


class AppUsageRead(AppUsageCreate):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    daily_usage_id: uuid.UUID


class DailyUsageCreate(BaseModel):
    date: date
    total_usage_minutes: int = Field(ge=0)
    session_count: int | None = Field(default=None, ge=0)
    night_usage_minutes: int = Field(ge=0)
    max_session_minutes: int | None = Field(default=None, ge=0)
    unlock_count: int | None = Field(default=None, ge=0)
    applications: list[AppUsageCreate] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_minute_relationships(self):
        if self.night_usage_minutes > self.total_usage_minutes:
            raise ValueError("night_usage_minutes cannot exceed total_usage_minutes")
        if self.max_session_minutes is not None and self.max_session_minutes > self.total_usage_minutes:
            raise ValueError("max_session_minutes cannot exceed total_usage_minutes")
        return self


class DailyUsageRead(DailyUsageCreate):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    profile_id: uuid.UUID
    created_at: datetime
    applications: list[AppUsageRead]

