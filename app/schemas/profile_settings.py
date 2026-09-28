import uuid
from datetime import datetime, time

from pydantic import BaseModel, ConfigDict, model_validator


class ProfileSettingsUpdate(BaseModel):
    school_start: time
    school_end: time
    sleep_start: time | None = None
    sleep_end: time | None = None

    @model_validator(mode="after")
    def validate_time_ranges(self):
        if (self.sleep_start is None) != (self.sleep_end is None):
            raise ValueError("sleep_start and sleep_end must both be provided or both be null")
        if self.school_start == self.school_end:
            raise ValueError("school_start and school_end must be different")
        if self.sleep_start is not None and self.sleep_start == self.sleep_end:
            raise ValueError("sleep_start and sleep_end must be different")
        return self


class ProfileSettingsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    profile_id: uuid.UUID
    school_start: time | None = None
    school_end: time | None = None
    sleep_start: time | None = None
    sleep_end: time | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
