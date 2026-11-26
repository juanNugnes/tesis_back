"""Strict DTOs for the Analytics adult-messages v1 contract."""

import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)


Identifier = Annotated[StrictStr, Field(min_length=1, max_length=128)]
Fingerprint = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]


class _AnalyticsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class AdultMessageDay(_AnalyticsModel):
    date: date
    revision: Annotated[StrictInt, Field(ge=1)]
    minutes: StrictInt | StrictFloat | None
    observed: StrictBool
    quality_verified: StrictBool


class AdultMessageEvaluationRequest(_AnalyticsModel):
    request_id: Identifier
    profile_id: Identifier
    series_id: Identifier
    time_zone: StrictStr
    evaluation_date: date
    assignment_verified: StrictBool
    shared_phone: StrictBool
    context_declared: StrictBool
    days: Annotated[list[AdultMessageDay], Field(min_length=28, max_length=28)]
    c1_evidence_ids: Annotated[list[Identifier], Field(max_length=366)]

    @field_validator("request_id", "profile_id", "series_id")
    @classmethod
    def identifiers_have_no_outer_whitespace(cls, value: str) -> str:
        if value.strip() != value:
            raise ValueError("identifier must not contain outer whitespace")
        return value

    @model_validator(mode="after")
    def evaluation_window_is_exact(self):
        try:
            zone = ZoneInfo(self.time_zone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("time_zone must be a recognized IANA zone") from None
        if self.evaluation_date.weekday() != 0:
            raise ValueError("evaluation_date must be a Monday")
        if self.evaluation_date > datetime.now(zone).date():
            raise ValueError("evaluation_date must not be in the future")
        expected = [
            self.evaluation_date - timedelta(days=28 - index)
            for index in range(28)
        ]
        if [row.date for row in self.days] != expected:
            raise ValueError(
                "evaluation must contain the 28 consecutive prior local days"
            )
        for row in self.days:
            if row.observed:
                if row.minutes is None or row.minutes < 0:
                    raise ValueError("observed days require non-negative minutes")
                start = datetime.combine(row.date, time.min, tzinfo=zone).astimezone(UTC)
                end = datetime.combine(
                    row.date + timedelta(days=1), time.min, tzinfo=zone
                ).astimezone(UTC)
                if row.minutes > (end - start).total_seconds() / 60:
                    raise ValueError("minutes exceed the duration of the local day")
            elif row.minutes is not None:
                raise ValueError("unobserved days require minutes=null")
        if len(self.c1_evidence_ids) != len(set(self.c1_evidence_ids)):
            raise ValueError("c1_evidence_ids must not contain duplicates")
        if self.c1_evidence_ids != sorted(self.c1_evidence_ids):
            raise ValueError("c1_evidence_ids must be sorted")
        encoded = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        if len(encoded) > 16_384:
            raise ValueError("evaluation request exceeds the Analytics HTTP limit")
        return self


class ControlledAdultMessageFixture(_AnalyticsModel):
    fixture_type: Literal["modera-adult-messages-controlled-v1"]
    fictional: Literal[True]
    fixture_id: Identifier
    payload: AdultMessageEvaluationRequest

    @field_validator("fixture_id")
    @classmethod
    def fixture_identifier_is_explicit(cls, value: str) -> str:
        if value.strip() != value or not value.startswith("fictitious-"):
            raise ValueError("fixture_id must use the fictitious- prefix")
        return value


class AdultMessageStatistics(_AnalyticsModel):
    recent: StrictFloat
    reference: StrictFloat
    delta: StrictFloat
    minimum: StrictFloat
    maximum: StrictFloat
    above: StrictFloat
    below: StrictFloat
    percent_change: StrictFloat | None
    observed_days_recent: StrictInt
    observed_days_reference: StrictInt


class AdultMessageCard(_AnalyticsModel):
    card_id: Fingerprint
    profile_id: Identifier
    series_id: Identifier
    time_zone: StrictStr
    direction: Literal["INCREASE", "DECREASE"]
    status: Literal[
        "ACTIVE", "INCONCLUSIVE", "INSUFFICIENT_DATA", "CONTEXT_REVIEW",
        "RESOLVED", "EXPIRED", "SERIES_CHANGED", "WITHDRAWN", "CORRECTED_HISTORY",
    ]
    title: StrictStr
    text: StrictStr
    recommendation: StrictStr
    evaluation_date: date
    opening_reference_start: date
    opening_recent_start: date
    comparison_recent_start: date
    statistics: AdultMessageStatistics | None
    measurement: StrictStr
    measurement_detail: StrictStr
    configuration_id: Literal["adult-messages-internal-candidate-v1"]
    configuration_fingerprint: Fingerprint
    scope: Literal["internal_candidate_not_family_validated"]
    delivery_status: Literal["CANDIDATE_NOT_DELIVERED"]
    corrected_dates: list[date] | None = None
    updated_date: date | None = None


class AdultMessageVersion(_AnalyticsModel):
    version: Annotated[StrictInt, Field(ge=1)]
    card: AdultMessageCard


class AdultMessageHistory(_AnalyticsModel):
    versions: list[AdultMessageVersion]

    @model_validator(mode="after")
    def unique_versions(self):
        identities = [(item.card.card_id, item.version) for item in self.versions]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate card version")
        return self


class AdultMessageConfiguration(_AnalyticsModel):
    c1_role: Literal["complementary_technical_evidence"]
    cooldown_days: StrictInt
    increase_minutes: StrictInt
    limit_period_days: StrictInt
    maximum_episode_days: StrictInt
    maximum_new_cards: StrictInt
    minimum_direction_days: StrictInt
    pmum_role: Literal["none"]
    return_margin_minutes: StrictInt
    scope: Literal["internal_candidate_not_family_validated"]
    version: Literal["adult-messages-internal-candidate-v1"]
    window_days: StrictInt


class AdultMessageHistoricalCardCorrection(_AnalyticsModel):
    card_id: Fingerprint
    version: Annotated[StrictInt, Field(ge=1)]
    status: Literal["WITHDRAWN", "CORRECTED_HISTORY"]


class AdultMessageEvaluationReceipt(_AnalyticsModel):
    status: Literal[
        "NO_CARD", "ACTIVE", "INCONCLUSIVE", "INSUFFICIENT_DATA",
        "CONTEXT_REVIEW", "RESOLVED", "EXPIRED", "SERIES_CHANGED",
        "WITHDRAWN", "CORRECTED_HISTORY", "COOLDOWN", "CARD_LIMIT",
    ]
    action: Literal["CREATE", "UPDATE", "NONE"]
    card: AdultMessageCard | None
    configuration: AdultMessageConfiguration
    c1_evidence_ids: list[Identifier]
    c1_role: Literal["complementary_technical_evidence"]
    push_notification: Literal[False]
    corrected_dates: list[date]
    historical_card_corrections: list[AdultMessageHistoricalCardCorrection]
    c1_correction_warning_since: date | None
    replayed: StrictBool
    evaluation_id: Fingerprint
    card_version: Annotated[StrictInt, Field(ge=1)] | None
