import enum

from pydantic import BaseModel, ConfigDict, Field


class ActivitySuggestionContext(str, enum.Enum):
    VOLUNTARY = "voluntary"
    BEDTIME = "bedtime"


class ActivitySuggestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context: ActivitySuggestionContext


class ActivitySuggestionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    activity_id: str
    title: str
    description: str
    category: str
    activity_level: str
    bedtime_suitable: bool
    requires_other_person: bool
    may_require_device_interaction: bool


class ActivitySuggestionsRead(BaseModel):
    context: ActivitySuggestionContext
    activities: list[ActivitySuggestionRead] = Field(max_length=3)
