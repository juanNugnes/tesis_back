import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.db.models import RiskLevel


class AnalysisCreate(BaseModel):
    date: date


class AnalysisRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    profile_id: uuid.UUID
    daily_usage_id: uuid.UUID
    analyzed_at: datetime
    anomaly_detected: bool
    anomaly_score: float | None
    risk_level: RiskLevel
    explanation: str | None
    model_version: str | None

