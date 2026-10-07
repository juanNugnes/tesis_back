import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import (
    ExperimentalPmumEstimateStatus,
    ParentalRespondent,
)


class ParentalScreenTimeResponseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sex_code: int | None
    weekday_category: int | None
    weekend_category: int | None
    respondent: ParentalRespondent
    reference_days: int = Field(gt=0)


class ParentalScreenTimeResponseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    profile_id: uuid.UUID
    adult_id: uuid.UUID
    revision: int
    age: int
    sex_code: int | None
    weekday_category: int | None
    weekend_category: int | None
    respondent: ParentalRespondent
    reference_days: int
    created_at: datetime
    updated_at: datetime


class ExperimentalPmumEstimateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    parental_response_id: uuid.UUID
    status: ExperimentalPmumEstimateStatus
    estimated_pmum_sf_mean: float | None
    model_version: str | None
    estimated_at: datetime
    technical_error_code: str | None
    technical_error_message: str | None
    created_at: datetime


class ExperimentalPmumReviewRead(BaseModel):
    response: ParentalScreenTimeResponseRead
    estimate: ExperimentalPmumEstimateRead | None
