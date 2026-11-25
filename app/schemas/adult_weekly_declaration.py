from __future__ import annotations

import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    field_validator,
    model_validator,
)


class AdultWeeklyDeclarationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: uuid.UUID
    time_zone: StrictStr
    effective_from: date
    effective_to: date | None = None
    assignment_verified: StrictBool
    shared_phone: StrictBool
    context_declared: StrictBool

    @field_validator("time_zone")
    @classmethod
    def valid_time_zone(cls, value: str) -> str:
        if (
            not 1 <= len(value) <= 128
            or "\\" in value
            or value.startswith("/")
            or any(part in {"", ".", ".."} for part in value.split("/"))
        ):
            raise ValueError("time_zone must be a valid IANA name")
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("time_zone must be a valid IANA name") from exc
        return value

    @model_validator(mode="after")
    def valid_period(self):
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not precede effective_from")
        return self


class AdultWeeklyDeclarationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: uuid.UUID
    profile_id: uuid.UUID
    device_id: uuid.UUID
    created_by_adult_id: uuid.UUID
    revision: int = Field(ge=1)
    time_zone: str | None
    effective_from: date
    effective_to: date | None
    assignment_verified: bool
    shared_phone: bool
    context_declared: bool
    created_at: datetime
    revoked_at: datetime | None
    revoked_effective_date: date | None
    revoked_by_adult_id: uuid.UUID | None
