"""HTTP v1 coordinator envelopes; test evidence is explicit, never inferred."""

from datetime import datetime
from typing import Annotated, Literal

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


class ChildOfferEventReceipt(ContractModel):
    status: Literal["recorded"]
    offer: ChildOfferRecord
    policy_version: StrictStr
    replayed: StrictBool


class ChildOfferHistory(ContractModel):
    model_config = ConfigDict(extra="allow", strict=True)
    offers: list[ChildOfferRecord]
    events: list[dict]
