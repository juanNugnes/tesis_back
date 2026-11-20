import math
import re
import uuid
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.db.models import HistoricalSubmissionStatus


HISTORICAL_MEASUREMENT_VERSION = "capture-test-v1"
HISTORICAL_BRIDGE_VERSION = "f12-backend-bridge-v1-candidate"
HISTORICAL_REPRESENTATION_SPEC_ID = "daily_use_duration_minutes_v2"
HISTORICAL_SOURCE_ID = "android-usage-events"
HISTORICAL_SOURCE_SEMANTICS = (
    "Interactive screen duration on assigned device; user attribution unverified"
)
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def validate_identifier(value: str) -> str:
    if not _IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError("must be a 1..128 character ASCII identifier")
    return value


def validate_time_zone_name(value: str) -> str:
    if (
        not 1 <= len(value) <= 128
        or "\\" in value
        or value.startswith("/")
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError("must be a valid IANA time zone")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("must be a valid IANA time zone") from None
    return value


class HistoricalCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: float | None
    basis: str | None = Field(max_length=256)

    @field_validator("value", mode="before")
    @classmethod
    def validate_value(cls, value):
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or not 0 <= value <= 1
        ):
            raise ValueError("coverage value must be finite and between 0 and 1")
        return value

    @field_validator("basis")
    @classmethod
    def validate_basis(cls, value: str | None):
        if value is not None and not value.strip():
            raise ValueError("coverage basis must not be blank")
        return value


class HistoricalProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: Literal[HISTORICAL_SOURCE_ID]
    source_version: Literal[HISTORICAL_BRIDGE_VERSION]
    source_semantics: Literal[HISTORICAL_SOURCE_SEMANTICS]
    adapter_version: Literal["v2"]
    input_fingerprint: str = Field(min_length=1, max_length=256)

    @field_validator("input_fingerprint")
    @classmethod
    def validate_input_fingerprint(cls, value: str):
        if not value.strip():
            raise ValueError("input_fingerprint must not be blank")
        return value


class PreparedHistoricalRepresentation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    representation_record_id: str
    representation_spec_id: Literal[HISTORICAL_REPRESENTATION_SPEC_ID]
    subject_id: str
    phenomenon: Literal["DAILY_USE_DURATION"]
    interval_start: datetime
    interval_end: datetime
    value: float | None
    unit: Literal["minutes"]
    status: Literal["OBSERVED", "OBSERVED_ZERO", "MISSING", "NOT_APPLICABLE"]
    coverage: HistoricalCoverage
    quality_flags: list[str] = Field(max_length=64)
    provenance: HistoricalProvenance
    computed_at: datetime

    @field_validator("representation_record_id", "subject_id")
    @classmethod
    def validate_caller_identifiers(cls, value: str):
        return validate_identifier(value)

    @field_validator("interval_start", "interval_end", "computed_at")
    @classmethod
    def validate_aware_datetime(cls, value: datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("must include a time zone")
        return value

    @field_validator("value", mode="before")
    @classmethod
    def validate_numeric_value_type(cls, value):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            raise ValueError("value must be a JSON number or null")
        return value

    @field_validator("quality_flags")
    @classmethod
    def validate_quality_flags(cls, values: list[str]):
        if any(not value.strip() or len(value) > 256 for value in values):
            raise ValueError("quality flags must contain non-empty strings up to 256 characters")
        return values

    @model_validator(mode="after")
    def validate_representation(self):
        if self.interval_start >= self.interval_end:
            raise ValueError("interval_start must be before interval_end")
        if self.computed_at < self.interval_end:
            raise ValueError("computed_at must be at or after interval_end")
        if self.value is not None and (
            isinstance(self.value, bool)
            or not math.isfinite(self.value)
            or self.value < 0
            or self.value > (self.interval_end - self.interval_start).total_seconds() / 60
        ):
            raise ValueError("value must be a finite duration within the interval")
        if self.status in {"MISSING", "NOT_APPLICABLE"} and self.value is not None:
            raise ValueError("missing representations require value=null")
        if self.status == "OBSERVED_ZERO" and self.value != 0:
            raise ValueError("OBSERVED_ZERO requires value=0")
        if self.status == "OBSERVED" and (self.value is None or self.value == 0):
            raise ValueError("OBSERVED requires a positive value")
        return self


class HistoricalSubmissionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    time_zone: str
    measurement_version: Literal[HISTORICAL_MEASUREMENT_VERSION]
    representation: PreparedHistoricalRepresentation

    @field_validator("request_id")
    @classmethod
    def validate_request_id(cls, value: str):
        return validate_identifier(value)

    @field_validator("time_zone")
    @classmethod
    def validate_time_zone(cls, value: str):
        return validate_time_zone_name(value)


class HistoricalSubmissionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    request_id: str
    profile_id: uuid.UUID
    device_id: uuid.UUID
    assignment_id: uuid.UUID
    local_date: date
    time_zone: str
    measurement_version: str
    status: HistoricalSubmissionStatus
    attempts: int = Field(ge=0)
    last_attempt_at: datetime | None
    analytics_evaluation_id: str | None
    analytics_replayed: bool | None
    retryable: bool
    last_error_code: str | None
    last_error_message: str | None
    created_at: datetime
    updated_at: datetime


class HistoricalAnalyticsEvaluationRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["ABSTAIN", "NO_CHANGE", "CHANGE"]
    abstention_reason: str | None = None


class HistoricalSubmissionSummaryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    local_date: date
    status: HistoricalSubmissionStatus
    attempts: int = Field(ge=0)
    analytics_evaluation_id: str | None
    analytics_evaluation: HistoricalAnalyticsEvaluationRead | None = None
    retryable: bool
    last_error_code: str | None
    last_error_message: str | None

    @classmethod
    def from_submission(
        cls,
        submission,
    ) -> "HistoricalSubmissionSummaryRead":
        evaluation = None
        response = submission.analytics_response
        if submission.status == HistoricalSubmissionStatus.PROCESSED and isinstance(
            response, dict
        ):
            result = response.get("result")
            raw_evaluation = result.get("evaluation") if isinstance(result, dict) else None
            if isinstance(raw_evaluation, dict):
                outcome = raw_evaluation.get("outcome")
                if outcome in {"ABSTAIN", "NO_CHANGE", "CHANGE"}:
                    reason = raw_evaluation.get("abstention_reason")
                    if reason is None or isinstance(reason, str):
                        evaluation = HistoricalAnalyticsEvaluationRead(
                            outcome=outcome,
                            abstention_reason=reason,
                        )

        return cls(
            id=submission.id,
            local_date=submission.local_date,
            status=submission.status,
            attempts=submission.attempts,
            analytics_evaluation_id=submission.analytics_evaluation_id,
            analytics_evaluation=evaluation,
            retryable=submission.retryable,
            last_error_code=submission.last_error_code,
            last_error_message=submission.last_error_message,
        )
