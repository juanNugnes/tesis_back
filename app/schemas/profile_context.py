import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.db.models import ProfileContextType


class ProfileContextCreate(BaseModel):
    type: ProfileContextType
    start_date: date
    end_date: date | None = None
    description: str | None = None


class ProfileContextUpdate(BaseModel):
    type: ProfileContextType | None = None
    start_date: date | None = None
    end_date: date | None = None
    description: str | None = None


class ProfileContextRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    profile_id: uuid.UUID
    type: ProfileContextType
    start_date: date
    end_date: date | None
    description: str | None
    created_at: datetime
    updated_at: datetime
