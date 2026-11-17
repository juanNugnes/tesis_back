"""Strict coordinator contracts; capture and server evidence stay explicit."""

from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import uuid

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator, model_validator


Identifier = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")]
Timestamp = StrictStr


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ChildOfferEvidence(ContractModel):
    profile_id: Identifier
    series_id: Identifier
    interval_id: Identifier
    now: Timestamp
    interval_started_at: Timestamp
    bedtime_at: Timestamp
    automatic_enabled: StrictBool
    assignment_verified: StrictBool
    shared_phone: StrictBool
    capture_verified: StrictBool
    screen_interactive: StrictBool
    game_active: StrictBool
    in_school_period: StrictBool
    eligibility_state: Literal["known", "unknown"]
    eligibility: dict | None = None
    interests: list[StrictStr] = Field(default_factory=list)

    @field_validator("now", "interval_started_at", "bedtime_at")
    @classmethod
    def aware_timestamp(cls, value: str) -> str:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError("timestamp must include an offset")
        return value

    @model_validator(mode="after")
    def eligibility_consistent(self):
        if (self.eligibility_state == "known") != (self.eligibility is not None):
            raise ValueError("known eligibility requires explicit filters; unknown must omit them")
        return self


class ChildOfferEvaluation(ContractModel):
    request_id: Identifier
    evidence: ChildOfferEvidence


class AdaptiveChildOfferEvaluation(ContractModel):
    request_id: Identifier
    evidence: ChildOfferEvidence
    pmum_parent_answers: dict


class AutomaticOpportunityCreate(BaseModel):
    """Capture facts supplied by Android; adult/profile facts are resolved server-side."""

    model_config = ConfigDict(extra="forbid")

    request_id: Identifier
    series_id: Identifier
    interval_id: Identifier
    observed_at: datetime
    interval_started_at: datetime
    timezone: StrictStr
    capture_verified: StrictBool
    screen_interactive: StrictBool
    game_active: StrictBool | None = None

    @field_validator("observed_at", "interval_started_at")
    @classmethod
    def aware_datetime(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("timestamp must include an offset")
        return value

    @field_validator("timezone")
    @classmethod
    def iana_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA identifier") from exc
        return value

    @model_validator(mode="after")
    def continuous_interval(self):
        if self.interval_started_at > self.observed_at:
            raise ValueError("interval_started_at must not be after observed_at")
        if self.observed_at - self.interval_started_at < timedelta(minutes=45):
            raise ValueError("continuous interval must be at least 45 minutes")
        zone = ZoneInfo(self.timezone)
        for field_name in ("observed_at", "interval_started_at"):
            value = getattr(self, field_name)
            expected_offset = value.astimezone(zone).utcoffset()
            if value.utcoffset() != expected_offset:
                raise ValueError(f"{field_name} offset does not match timezone")
        if self.observed_at > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError("observed_at is too far in the future")
        return self


class ChildOfferEvent(ContractModel):
    request_id: Identifier
    profile_id: Identifier
    offer_id: Identifier
    event: Literal["delivered", "delivery_failed", "selected", "rejected", "postponed", "dismissed"]
    occurred_at: Timestamp
    now: Timestamp
    channel: Literal["notification", "overlay"] | None = None
    selected_activity_id: Identifier | None = None

    @field_validator("occurred_at", "now")
    @classmethod
    def aware_timestamp(cls, value: str) -> str:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError("timestamp must include an offset")
        return value

    @model_validator(mode="after")
    def event_fields(self):
        if (self.event == "delivered") != (self.channel is not None):
            raise ValueError("channel is required only for delivery")
        if (self.event == "selected") != (self.selected_activity_id is not None):
            raise ValueError("selected_activity_id is required only for selection")
        return self


class ChildOfferRecord(BaseModel):
    """Validate identity and essential state while retaining the library's exact DTO."""

    model_config = ConfigDict(extra="allow")
    offer_id: Identifier
    profile_id: Identifier
    status: StrictStr
    expires_at: StrictStr


NonEmptyText = Annotated[StrictStr, Field(min_length=1)]


class AutomaticOfferActivity(BaseModel):
    """Published catalog content required by Android before presentation."""

    model_config = ConfigDict(extra="allow", strict=True)

    activity_id: Identifier
    title: NonEmptyText
    description: NonEmptyText
    category: NonEmptyText
    materials: list[NonEmptyText] | None = None
    steps: list[NonEmptyText] | None = None


class AutomaticOfferSelection(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    activities: list[AutomaticOfferActivity] = Field(min_length=1, max_length=3)


class AutomaticOfferRecord(ChildOfferRecord):
    model_config = ConfigDict(extra="allow", strict=True)

    activities_offered: list[Identifier] = Field(min_length=1, max_length=3)
    selection: AutomaticOfferSelection

    @model_validator(mode="after")
    def content_matches_identifiers(self):
        selected_ids = [activity.activity_id for activity in self.selection.activities]
        if len(self.activities_offered) != len(set(self.activities_offered)):
            raise ValueError("offered activity identifiers must be unique")
        if selected_ids != self.activities_offered:
            raise ValueError("catalog content must match offered activity identifiers")
        return self


class ChildOfferEvaluationReceipt(ContractModel):
    status: Literal["offer", "suppressed"]
    reason: StrictStr | None = None
    offer_id: Identifier | None = None
    offer: ChildOfferRecord | None = None
    channel: Literal["notification", "overlay"] | None = None
    policy_version: StrictStr
    replayed: StrictBool

    @model_validator(mode="after")
    def valid_offer(self):
        if self.status == "offer" and (self.offer is None or self.channel is None):
            raise ValueError("offer receipt is incomplete")
        return self


class AdaptiveChildOfferEvaluationReceipt(ContractModel):
    status: Literal["offer", "suppressed"]
    reason: StrictStr | None = None
    pmum_reason: StrictStr | None = None
    offer_id: Identifier | None = None
    # The internal client preserves the candidate response. The public Android
    # contract is validated and narrowed to AutomaticOfferRecord by the service.
    offer: ChildOfferRecord | None = None
    channel: Literal["notification", "overlay"] | None = None
    policy_version: StrictStr
    replayed: StrictBool

    @model_validator(mode="after")
    def valid_offer(self):
        if self.status == "offer" and (self.offer is None or self.channel is None):
            raise ValueError("offer receipt is incomplete")
        return self


class AutomaticOpportunityRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["offer_available", "discarded"]
    request_id: Identifier
    reason: str | None = None
    replayed: bool = False
    policy_version: str | None = None
    coordinator_offer_id: Identifier | None = None
    activity_offer_id: uuid.UUID | None = None
    channel: Literal["notification", "overlay"] | None = None
    offer: AutomaticOfferRecord | None = None


class AutomaticOfferSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    automatic_enabled: StrictBool
    shared_phone: StrictBool


class AutomaticOfferSettingsRead(AutomaticOfferSettingsUpdate):
    model_config = ConfigDict(from_attributes=True, extra="forbid", strict=True)

    profile_id: uuid.UUID
    updated_at: datetime


class ChildOfferEventReceipt(ContractModel):
    status: Literal["recorded"]
    offer: ChildOfferRecord
    policy_version: StrictStr
    replayed: StrictBool


class ChildOfferHistory(ContractModel):
    model_config = ConfigDict(extra="allow", strict=True)
    offers: list[ChildOfferRecord]
    events: list[dict]
