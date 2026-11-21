import re
import uuid
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)


F12_EVIDENCE_ENVELOPE_VERSION = 1
F12_EXPORT_ADAPTER_VERSION = "f12-android-export-adapter-v1-candidate"
F12_RAW_EVENT_SCHEMA_VERSION = "raw-usage-event-v1"
F12_MAX_SOURCE_BYTES = 32 * 1024 * 1024
F12_MAX_HTTP_BODY_BYTES = 48 * 1024 * 1024
F12_MAX_MANIFEST_BYTES = 64 * 1024
F12_MAX_PAIRS = 10_000
F12_MAX_EVENTS_PER_PAIR = 100_000
F12_UNAVAILABLE_REASON = "NOT_EXPORTED"

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class F12EvidencePairManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    collector_run_id: StrictStr
    summary_sha256: StrictStr
    events_sha256: StrictStr

    @field_validator("collector_run_id")
    @classmethod
    def validate_collector_run_id(cls, value: str) -> str:
        try:
            parsed = uuid.UUID(value)
        except ValueError as exc:
            raise ValueError("collector_run_id must be a canonical UUID v4") from exc
        if parsed.version != 4 or str(parsed) != value:
            raise ValueError("collector_run_id must be a canonical UUID v4")
        return value

    @field_validator("summary_sha256", "events_sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if not _SHA256_PATTERN.fullmatch(value):
            raise ValueError("must be a lowercase SHA-256 hex digest")
        return value


class F12EvidenceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    envelope_version: Literal[F12_EVIDENCE_ENVELOPE_VERSION]
    capture_id: StrictStr
    adapter_version: Literal[F12_EXPORT_ADAPTER_VERSION]
    local_date: date
    time_zone: StrictStr
    revision: StrictInt = Field(gt=0)
    created_at_epoch_millis: StrictInt = Field(ge=0)
    content_sha256: StrictStr
    evidence_state: Literal["AVAILABLE", "UNAVAILABLE"]
    unavailable_reason: Literal["NOT_EXPORTED"] | None = None
    pairs: list[F12EvidencePairManifest]

    @field_validator("capture_id", "content_sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if not _SHA256_PATTERN.fullmatch(value):
            raise ValueError("must be a lowercase SHA-256 hex digest")
        return value

    @field_validator("local_date", mode="before")
    @classmethod
    def validate_local_date_input(cls, value):
        if not isinstance(value, str):
            raise ValueError("local_date must be an ISO date string")
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("local_date must be a valid ISO date") from exc

    @field_validator("time_zone")
    @classmethod
    def validate_time_zone(cls, value: str) -> str:
        if (
            not 1 <= len(value) <= 128
            or "\\" in value
            or value.startswith("/")
            or any(part in {"", ".", ".."} for part in value.split("/"))
        ):
            raise ValueError("time_zone must be a valid IANA name")
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("time_zone must be a valid IANA name") from exc
        return value

    @field_validator("pairs", mode="before")
    @classmethod
    def validate_pairs_are_json_array(cls, value):
        if not isinstance(value, list):
            raise ValueError("pairs must be a JSON array")
        return value

    @model_validator(mode="after")
    def validate_state(self):
        if len(self.pairs) > F12_MAX_PAIRS:
            raise ValueError("too many export pairs")
        if self.evidence_state == "AVAILABLE":
            if not self.pairs or self.unavailable_reason is not None:
                raise ValueError("AVAILABLE evidence requires pairs and no unavailable_reason")
        elif self.pairs or self.unavailable_reason != F12_UNAVAILABLE_REASON:
            raise ValueError(
                "UNAVAILABLE evidence requires no pairs and unavailable_reason=NOT_EXPORTED"
            )
        run_ids = [pair.collector_run_id for pair in self.pairs]
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("collector_run_id must be unique within a capture")
        return self


class F12EvidenceReceipt(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    receipt_id: uuid.UUID
    capture_id: str
    status: Literal["RECEIVED"]
    evidence_state: Literal["AVAILABLE", "UNAVAILABLE"]
    local_date: date
    revision: int
    content_sha256: str
    pair_count: int
    total_bytes: int
    received_at: datetime
    expires_at: datetime | None
