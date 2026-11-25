from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict


F12DayStatus = Literal["OBSERVED", "OBSERVED_ZERO", "MISSING", "NOT_APPLICABLE"]


class _DiagnosticModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AdultMessageSeriesCandidate(_DiagnosticModel):
    series_id: str
    device_id: uuid.UUID
    time_zone: str
    measurement_version: str
    prepared_day_count: int
    first_date: date
    last_date: date


class AdultMessageWindowDay(_DiagnosticModel):
    date: date
    period: Literal["REFERENCE", "RECENT"]
    evidence_available: bool
    raw_evidence_state: Literal["ABSENT", "AVAILABLE", "UNAVAILABLE"]
    unavailable_reason: str | None
    evidence_id: uuid.UUID | None
    revision: int | None
    available_revisions: list[int]
    f12_status: F12DayStatus
    f12_quality_flags: list[str]
    minutes: float | None
    quality_verified: Literal[False] = False
    preparation_id: uuid.UUID | None
    historical_submission_id: uuid.UUID | None
    historical_submission_status: str | None
    c1_evaluation_id: str | None
    candidate_evidence_count: int
    problems: list[str]


class AdultMessageContextRecord(_DiagnosticModel):
    type: str
    start_date: date
    end_date: date | None


class AdultMessageHistoricalDeclaration(_DiagnosticModel):
    contract_value: bool | None = None
    historical_records_present: bool
    complete_for_window: bool
    current_state_present: bool
    problem: str


class AdultMessageDeclarationDiagnostic(_DiagnosticModel):
    assignment: AdultMessageHistoricalDeclaration
    shared_phone: AdultMessageHistoricalDeclaration
    family_context: AdultMessageHistoricalDeclaration
    context_records: list[AdultMessageContextRecord]


class AdultMessageWindowBlocker(_DiagnosticModel):
    code: str
    dates: list[date]
    detail: str


class AdultMessageWindowDiagnostic(_DiagnosticModel):
    profile_id: uuid.UUID
    evaluation_date: date
    window_start: date
    window_end: date
    reference_start: date
    reference_end: date
    recent_start: date
    recent_end: date
    selected_series: AdultMessageSeriesCandidate | None
    candidate_series: list[AdultMessageSeriesCandidate]
    days: list[AdultMessageWindowDay]
    declarations: AdultMessageDeclarationDiagnostic
    available_day_count: int
    observed_day_count: int
    missing_day_count: int
    evaluation_ready: bool
    blockers: list[AdultMessageWindowBlocker]
