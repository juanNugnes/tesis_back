import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.models import ActivityInterest, ActivityToAvoid, AdaptationType, FunctionalConsideration


class ProfileActivityPreferencesUpdate(BaseModel):
    interests: list[ActivityInterest] = Field(default_factory=list)
    adaptation_type: AdaptationType = AdaptationType.NONE
    considerations: list[FunctionalConsideration] = Field(default_factory=list)
    activities_to_avoid: list[ActivityToAvoid] = Field(default_factory=list)
    additional_notes: str | None = Field(default=None, max_length=500)
    valid_until: date | None = None

    @field_validator("interests", "considerations", "activities_to_avoid")
    @classmethod
    def validate_unique_values(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("duplicate values are not allowed")
        return values


class ProfileActivityPreferencesRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID | None = None
    profile_id: uuid.UUID
    interests: list[ActivityInterest] = Field(default_factory=list)
    adaptation_type: AdaptationType = AdaptationType.NONE
    considerations: list[FunctionalConsideration] = Field(default_factory=list)
    activities_to_avoid: list[ActivityToAvoid] = Field(default_factory=list)
    additional_notes: str | None = None
    valid_until: date | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
