import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ActivitySelectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selection_id: uuid.UUID
    offer_id: uuid.UUID
    activity_id: str = Field(min_length=1, max_length=100)
    selected_at_epoch_millis: int = Field(ge=0)


class ActivitySelectionRead(ActivitySelectionCreate):
    model_config = ConfigDict(from_attributes=True)

    profile_id: uuid.UUID
    device_id: uuid.UUID
    created_at: datetime


class ActivitySelectionCountRead(BaseModel):
    count: int = Field(ge=0)
