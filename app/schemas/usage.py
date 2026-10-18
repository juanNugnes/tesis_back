import uuid
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class UsageMetricStatus(str, Enum):
    MEASURED = "MEASURED"
    UNAVAILABLE = "UNAVAILABLE"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    CAPTURE_ERROR = "CAPTURE_ERROR"
    INCOMPLETE = "INCOMPLETE"


class UsageCoverageStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    PARTIAL = "PARTIAL"


class UsageMetricStatuses(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_usage_minutes: UsageMetricStatus
    session_count: UsageMetricStatus
    night_usage_minutes: UsageMetricStatus
    max_session_minutes: UsageMetricStatus
    unlock_count: UsageMetricStatus
    applications: UsageMetricStatus
    last_night_activity_at: UsageMetricStatus = UsageMetricStatus.UNAVAILABLE


class NightWindowDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["PROFILE_SLEEP_SCHEDULE"]
    start_local_time: time
    end_local_time: time

    @model_validator(mode="after")
    def validate_non_empty_window(self):
        if self.start_local_time == self.end_local_time:
            raise ValueError("night window start and end must differ")
        return self


class AppUsageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_name: str = Field(min_length=1, max_length=255)
    app_name: str | None = Field(default=None, max_length=150)
    category: str | None = Field(default=None, max_length=80)
    usage_minutes: int = Field(ge=0)


class AppUsageRead(AppUsageCreate):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    daily_usage_id: uuid.UUID


class DailyUsageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: uuid.UUID
    captured_at: datetime
    date: date
    period_start_at: datetime
    period_end_at: datetime
    timezone: str = Field(min_length=1, max_length=64)
    duration_unit: Literal["MINUTES"]
    measurement_method: Literal["ANDROID_USAGE_STATS_AGGREGATES"]
    calculation_version: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    coverage_status: UsageCoverageStatus
    total_usage_minutes: int | None = Field(default=None, ge=0)
    session_count: int | None = Field(default=None, ge=0)
    night_usage_minutes: int | None = Field(default=None, ge=0)
    last_night_activity_at: datetime | None = None
    max_session_minutes: int | None = Field(default=None, ge=0)
    unlock_count: int | None = Field(default=None, ge=0)
    applications: list[AppUsageCreate] | None = None
    metric_statuses: UsageMetricStatuses
    night_window: NightWindowDefinition | None = None

    @field_validator(
        "captured_at",
        "period_start_at",
        "period_end_at",
        "last_night_activity_at",
    )
    @classmethod
    def require_timezone_aware_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must include a UTC offset")
        return value

    @field_validator("timezone")
    @classmethod
    def require_iana_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA identifier") from exc
        return value

    @model_validator(mode="after")
    def validate_snapshot(self):
        zone = ZoneInfo(self.timezone)
        local_start = self.period_start_at.astimezone(zone)
        local_end = self.period_end_at.astimezone(zone)
        next_midnight = datetime.combine(self.date + timedelta(days=1), time.min, zone)
        if local_start.date() != self.date:
            raise ValueError("date must match period_start_at in the declared timezone")
        if self.period_end_at <= self.period_start_at:
            raise ValueError("period_end_at must be after period_start_at")
        if local_end > next_midnight:
            raise ValueError("period must not extend beyond the declared local day")
        if self.captured_at < self.period_end_at:
            raise ValueError("captured_at must not precede period_end_at")

        metric_pairs = (
            ("total_usage_minutes", self.total_usage_minutes),
            ("session_count", self.session_count),
            ("night_usage_minutes", self.night_usage_minutes),
            ("max_session_minutes", self.max_session_minutes),
            ("unlock_count", self.unlock_count),
            ("applications", self.applications),
            ("last_night_activity_at", self.last_night_activity_at),
        )
        for name, value in metric_pairs:
            status = getattr(self.metric_statuses, name)
            if status == UsageMetricStatus.MEASURED and value is None:
                raise ValueError(f"{name} must have a value when status is MEASURED")
            if status in {
                UsageMetricStatus.UNAVAILABLE,
                UsageMetricStatus.PERMISSION_DENIED,
                UsageMetricStatus.CAPTURE_ERROR,
            } and value is not None:
                raise ValueError(f"{name} must be null when status is {status.value}")

        last_activity_status = self.metric_statuses.last_night_activity_at
        if last_activity_status != UsageMetricStatus.MEASURED and self.last_night_activity_at is not None:
            raise ValueError("last_night_activity_at must be null unless status is MEASURED")
        if self.last_night_activity_at is not None:
            if self.night_window is None:
                raise ValueError("night_window is required when last_night_activity_at has a value")
            if not self.period_start_at <= self.last_night_activity_at < self.period_end_at:
                raise ValueError("last_night_activity_at must be inside the measured period")
            local_activity = self.last_night_activity_at.astimezone(zone).timetz().replace(tzinfo=None)
            night_start = self.night_window.start_local_time
            night_end = self.night_window.end_local_time
            inside_night = (
                night_start <= local_activity < night_end
                if night_start < night_end
                else local_activity >= night_start or local_activity < night_end
            )
            if not inside_night:
                raise ValueError("last_night_activity_at must be inside night_window")

        if self.night_usage_minutes is not None and self.night_window is None:
            raise ValueError("night_window is required when night usage has a value")
        if (
            self.night_usage_minutes is not None
            and self.total_usage_minutes is not None
            and self.night_usage_minutes > self.total_usage_minutes
        ):
            raise ValueError("night_usage_minutes cannot exceed total_usage_minutes")
        if (
            self.max_session_minutes is not None
            and self.total_usage_minutes is not None
            and self.max_session_minutes > self.total_usage_minutes
        ):
            raise ValueError("max_session_minutes cannot exceed total_usage_minutes")
        if self.applications is not None:
            packages = [item.package_name for item in self.applications]
            if len(packages) != len(set(packages)):
                raise ValueError("applications must contain unique package_name values")
        return self


class UsageSourceRead(BaseModel):
    snapshot_id: uuid.UUID
    device_id: uuid.UUID
    captured_at: datetime
    period_start_at: datetime
    period_end_at: datetime
    timezone: str
    duration_unit: Literal["MINUTES"]
    measurement_method: Literal["ANDROID_USAGE_STATS_AGGREGATES"]
    calculation_version: str
    coverage_status: UsageCoverageStatus
    total_usage_minutes: int | None
    session_count: int | None
    night_usage_minutes: int | None
    last_night_activity_at: datetime | None
    max_session_minutes: int | None
    unlock_count: int | None
    applications: list[AppUsageCreate] | None
    metric_statuses: UsageMetricStatuses
    night_window: NightWindowDefinition | None


class DailyUsageRead(BaseModel):
    id: uuid.UUID
    profile_id: uuid.UUID
    date: date
    total_usage_minutes: int | None
    session_count: int | None
    night_usage_minutes: int | None
    last_night_activity_at: datetime | None
    max_session_minutes: int | None
    unlock_count: int | None
    applications: list[AppUsageRead] | None
    metric_statuses: UsageMetricStatuses
    coverage_status: UsageCoverageStatus
    period_start_at: datetime | None
    period_end_at: datetime | None
    timezone: str | None
    duration_unit: Literal["MINUTES"]
    measurement_method: Literal["ANDROID_USAGE_STATS_AGGREGATES"] | None
    calculation_version: str | None
    night_window: NightWindowDefinition | None
    periods_uniform: bool
    source_count: int
    sources: list[UsageSourceRead]
    created_at: datetime
    updated_at: datetime


class UsageWriteResult(BaseModel):
    result: Literal["created", "updated", "unchanged"]
    usage: DailyUsageRead

