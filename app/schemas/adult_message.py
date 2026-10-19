"""DTO de historial del candidato Analytics adult-messages v1, sin reglas propias."""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr, model_validator


Identifier = Annotated[StrictStr, Field(min_length=1, max_length=128)]
Fingerprint = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]


class _AnalyticsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


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
