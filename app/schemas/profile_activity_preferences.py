import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.db.models import (
    ActivityConsiderationType,
    ActivityInterest,
    ActivityToAvoid,
    ConsiderationDurationType,
)


class ActivityConsiderationBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ActivityConsiderationType
    duration_type: ConsiderationDurationType
    valid_until: date | None = None

    @model_validator(mode="after")
    def validate_duration(self):
        if self.duration_type == ConsiderationDurationType.TEMPORARY:
            if self.valid_until is None:
                raise ValueError("valid_until is required for a temporary consideration")
        elif self.valid_until is not None:
            raise ValueError("valid_until must be null for a permanent consideration")
        return self


class ActivityConsiderationUpdate(ActivityConsiderationBase):
    @model_validator(mode="after")
    def validate_temporary_date(self):
        if self.valid_until is not None and self.valid_until < date.today():
            raise ValueError("valid_until cannot be earlier than today")
        return self


class ActivityConsiderationRead(ActivityConsiderationBase):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class ProfileActivityPreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interests: list[ActivityInterest] = Field(default_factory=list)
    considerations_answered: bool = False
    considerations: list[ActivityConsiderationUpdate] = Field(default_factory=list)
    activities_to_avoid: list[ActivityToAvoid] = Field(default_factory=list)
    additional_notes: str | None = Field(default=None, max_length=500)

    @field_validator("interests", "activities_to_avoid")
    @classmethod
    def validate_unique_values(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("duplicate values are not allowed")
        return values

    @model_validator(mode="after")
    def validate_considerations(self):
        types = [consideration.type for consideration in self.considerations]
        if len(types) != len(set(types)):
            raise ValueError("duplicate consideration types are not allowed")
        if not self.considerations_answered and self.considerations:
            raise ValueError(
                "considerations must be empty when considerations_answered is false"
            )
        return self


class ProfileActivityPreferencesRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID | None = None
    profile_id: uuid.UUID
    interests: list[ActivityInterest] = Field(default_factory=list)
    considerations_answered: bool = False
    considerations: list[ActivityConsiderationRead] = Field(default_factory=list)
    activities_to_avoid: list[ActivityToAvoid] = Field(default_factory=list)
    additional_notes: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
