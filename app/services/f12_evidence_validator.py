from __future__ import annotations

import hashlib
import json
import uuid
from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.schemas.f12_evidence import (
    F12EvidenceManifest,
    F12_MAX_EVENTS_PER_PAIR,
    F12_MAX_SOURCE_BYTES,
    F12_RAW_EVENT_SCHEMA_VERSION,
    F12_UNAVAILABLE_REASON,
)


SUMMARY_FIELDS = {
    "collectorRunId",
    "requestedBeginEpochMs",
    "requestedEndEpochMs",
    "collectedAtEpochMs",
    "usageAccessAvailable",
    "queryReturnedNull",
    "eventCount",
    "errorCode",
    "errorMessage",
}
EVENT_FIELDS = {
    "schemaVersion",
    "collectorRunId",
    "queryBeginEpochMs",
    "queryEndEpochMs",
    "collectedAtEpochMs",
    "eventTimeEpochMs",
    "eventTypeCode",
    "eventTypeName",
    "queryOrdinal",
    "packageName",
    "className",
    "androidApiLevel",
    "manufacturer",
    "model",
}
EVENT_NAMES = {
    0: "NONE",
    1: "ACTIVITY_RESUMED",
    2: "ACTIVITY_PAUSED",
    5: "CONFIGURATION_CHANGE",
    7: "USER_INTERACTION",
    8: "SHORTCUT_INVOCATION",
    11: "STANDBY_BUCKET_CHANGED",
    15: "SCREEN_INTERACTIVE",
    16: "SCREEN_NON_INTERACTIVE",
    17: "KEYGUARD_SHOWN",
    18: "KEYGUARD_HIDDEN",
    19: "FOREGROUND_SERVICE_START",
    20: "FOREGROUND_SERVICE_STOP",
    23: "ACTIVITY_STOPPED",
    26: "DEVICE_SHUTDOWN",
    27: "DEVICE_STARTUP",
}
COLLECTOR_STATES = {
    None: (True, False),
    "USAGE_ACCESS_NOT_GRANTED": (False, False),
    "QUERY_RETURNED_NULL": (True, True),
    "SECURITY_EXCEPTION": (True, False),
    "QUERY_RUNTIME_EXCEPTION": (True, False),
}
MAX_OVERLAP_COMPARISONS = 100_000


class F12EvidenceValidationError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class F12EvidencePairBytes:
    collector_run_id: str
    summary_bytes: bytes
    events_bytes: bytes
    summary_sha256: str
    events_sha256: str


@dataclass(frozen=True)
class ValidatedF12Evidence:
    manifest: F12EvidenceManifest
    pairs: tuple[F12EvidencePairBytes, ...]
    expected_capture_id: str
    total_bytes: int
    source_created_at: datetime


def strict_json_object(data: bytes | str) -> dict[str, Any]:
    def reject_constant(_value: str):
        raise ValueError("non-finite JSON number")

    def unique_object(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        text = data.decode("utf-8") if isinstance(data, bytes) else data
        value = json.loads(
            text,
            parse_constant=reject_constant,
            object_pairs_hook=unique_object,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise F12EvidenceValidationError(
            "invalid_json",
            "Evidence JSON must be strict UTF-8 JSON without duplicate keys",
        ) from exc
    if not isinstance(value, dict):
        raise F12EvidenceValidationError("invalid_json", "Evidence JSON must be an object")
    return value


def parse_manifest(data: bytes | str) -> F12EvidenceManifest:
    try:
        return F12EvidenceManifest.model_validate(strict_json_object(data))
    except ValidationError as exc:
        raise F12EvidenceValidationError(
            "invalid_manifest",
            "Evidence manifest does not match f12-raw-evidence-v1",
        ) from exc


def _event_name(code: int, api_level: int) -> str:
    if api_level < 29:
        if code in {19, 20, 23, 26, 27}:
            return f"EVENT_TYPE_{code}"
        if code in {1, 2}:
            return {1: "MOVE_TO_FOREGROUND", 2: "MOVE_TO_BACKGROUND"}[code]
    return EVENT_NAMES.get(code, f"EVENT_TYPE_{code}")


def _android_int(value: Any) -> int:
    if type(value) is not int or not -(2**63) <= value < 2**63:
        raise F12EvidenceValidationError(
            "invalid_export_contract",
            "Evidence contains an invalid Android integer",
        )
    return value


def _exact_object(data: bytes, fields: set[str]) -> dict[str, Any]:
    value = strict_json_object(data)
    if set(value) != fields:
        raise F12EvidenceValidationError(
            "invalid_export_contract",
            "Evidence fields do not match the F12 export contract",
        )
    return value


def _parse_pair(
    pair: F12EvidencePairBytes,
) -> tuple[dict[str, Any], list[dict[str, Any]], set[tuple[int, str, str]]]:
    if len(pair.summary_bytes) + len(pair.events_bytes) > F12_MAX_SOURCE_BYTES:
        raise F12EvidenceValidationError("evidence_too_large", "An export pair exceeds 32 MiB")
    if hashlib.sha256(pair.summary_bytes).hexdigest() != pair.summary_sha256:
        raise F12EvidenceValidationError("summary_hash_mismatch", "summary.json SHA-256 mismatch")
    if hashlib.sha256(pair.events_bytes).hexdigest() != pair.events_sha256:
        raise F12EvidenceValidationError("events_hash_mismatch", "events.jsonl SHA-256 mismatch")

    summary = _exact_object(pair.summary_bytes, SUMMARY_FIELDS)
    run_id = summary["collectorRunId"]
    if not isinstance(run_id, str):
        raise F12EvidenceValidationError(
            "invalid_export_contract", "collectorRunId must be a canonical UUID v4"
        )
    try:
        parsed_id = uuid.UUID(run_id)
    except ValueError as exc:
        raise F12EvidenceValidationError(
            "invalid_export_contract", "collectorRunId must be a canonical UUID v4"
        ) from exc
    if str(parsed_id) != run_id or parsed_id.version != 4 or run_id != pair.collector_run_id:
        raise F12EvidenceValidationError(
            "invalid_export_contract", "collectorRunId does not match the pair manifest"
        )

    for field in (
        "requestedBeginEpochMs",
        "requestedEndEpochMs",
        "collectedAtEpochMs",
        "eventCount",
    ):
        _android_int(summary[field])
    begin = summary["requestedBeginEpochMs"]
    end = summary["requestedEndEpochMs"]
    if begin >= end or summary["eventCount"] < 0 or summary["collectedAtEpochMs"] < end:
        raise F12EvidenceValidationError(
            "invalid_export_contract", "Summary bounds, count, or collection time are inconsistent"
        )
    for field in ("usageAccessAvailable", "queryReturnedNull"):
        if type(summary[field]) is not bool:
            raise F12EvidenceValidationError(
                "invalid_export_contract", "Capture state must use JSON booleans"
            )
    for field in ("errorCode", "errorMessage"):
        if summary[field] is not None and not isinstance(summary[field], str):
            raise F12EvidenceValidationError(
                "invalid_export_contract", "Collector error fields must be strings or null"
            )
    error_code = summary["errorCode"]
    state = (summary["usageAccessAvailable"], summary["queryReturnedNull"])
    if error_code not in COLLECTOR_STATES or COLLECTOR_STATES[error_code] != state:
        raise F12EvidenceValidationError(
            "invalid_export_contract", "Collector state is not recognized by the F12 contract"
        )
    if error_code is None and summary["errorMessage"] is not None:
        raise F12EvidenceValidationError(
            "invalid_export_contract", "Successful queries cannot contain an error message"
        )

    lines = pair.events_bytes.splitlines()
    if len(lines) > F12_MAX_EVENTS_PER_PAIR:
        raise F12EvidenceValidationError("too_many_events", "events.jsonl exceeds 100000 rows")
    rows = [_exact_object(line, EVENT_FIELDS) for line in lines]
    if len(rows) != summary["eventCount"] or (error_code is not None and rows):
        raise F12EvidenceValidationError(
            "invalid_export_contract", "Event count does not match the summary"
        )

    ordinals: set[int] = set()
    hardware: set[tuple[int, str, str]] = set()
    previous: tuple[int, int] | None = None
    for row in rows:
        if row["schemaVersion"] != F12_RAW_EVENT_SCHEMA_VERSION:
            raise F12EvidenceValidationError(
                "unsupported_event_schema", "Unsupported raw event schemaVersion"
            )
        for field in (
            "queryBeginEpochMs",
            "queryEndEpochMs",
            "collectedAtEpochMs",
            "eventTimeEpochMs",
            "eventTypeCode",
            "queryOrdinal",
            "androidApiLevel",
        ):
            _android_int(row[field])
        api_level = row["androidApiLevel"]
        event_type = row["eventTypeCode"]
        if not 28 <= api_level < 2**31 or not 0 <= event_type < 2**31:
            raise F12EvidenceValidationError(
                "invalid_export_contract", "Android API level or event type is invalid"
            )
        if (
            row["collectorRunId"] != run_id
            or row["queryBeginEpochMs"] != begin
            or row["queryEndEpochMs"] != end
            or row["collectedAtEpochMs"] != summary["collectedAtEpochMs"]
            or not begin <= row["eventTimeEpochMs"] < end
        ):
            raise F12EvidenceValidationError(
                "invalid_export_contract", "Event does not belong to its collector query"
            )
        if row["eventTypeName"] != _event_name(event_type, api_level):
            raise F12EvidenceValidationError(
                "invalid_export_contract", "Event name does not match its code and Android API"
            )
        for field in ("packageName", "className"):
            if row[field] is not None and not isinstance(row[field], str):
                raise F12EvidenceValidationError(
                    "invalid_export_contract", "Nullable event metadata is invalid"
                )
        for field in ("manufacturer", "model"):
            if not isinstance(row[field], str):
                raise F12EvidenceValidationError(
                    "invalid_export_contract", "Build metadata must be strings"
                )
        hardware.add((api_level, row["manufacturer"], row["model"]))
        ordinal = row["queryOrdinal"]
        order = (row["eventTimeEpochMs"], ordinal)
        if ordinal < 0 or ordinal in ordinals or (previous is not None and order < previous):
            raise F12EvidenceValidationError(
                "invalid_export_contract", "Event order or queryOrdinal is invalid"
            )
        ordinals.add(ordinal)
        previous = order
    if ordinals != set(range(len(rows))) or len(hardware) > 1:
        raise F12EvidenceValidationError(
            "invalid_export_contract", "Event enumeration or hardware metadata is inconsistent"
        )
    return summary, rows, hardware


def _validate_overlaps(queries: list[tuple[dict[str, Any], list[dict[str, Any]]]]) -> None:
    if not any(rows for _, rows in queries):
        return
    successful = sorted(
        (
            (summary, rows, [row["eventTimeEpochMs"] for row in rows])
            for summary, rows in queries
            if summary["errorCode"] is None
        ),
        key=lambda item: (item[0]["requestedBeginEpochMs"], item[0]["collectorRunId"]),
    )
    active = []
    comparisons = 0
    keys = (
        "schemaVersion",
        "eventTimeEpochMs",
        "eventTypeCode",
        "eventTypeName",
        "packageName",
        "className",
        "androidApiLevel",
        "manufacturer",
        "model",
    )

    def snapshot(rows, times, begin, end):
        return Counter(
            tuple(row[key] for key in keys)
            for row in rows[bisect_left(times, begin) : bisect_left(times, end)]
        )

    for summary, rows, times in successful:
        begin = summary["requestedBeginEpochMs"]
        end = summary["requestedEndEpochMs"]
        active = [
            item for item in active if item[0]["requestedEndEpochMs"] > begin
        ]
        for previous, earlier, earlier_times in active:
            comparisons += 1
            if comparisons > MAX_OVERLAP_COMPARISONS:
                raise F12EvidenceValidationError(
                    "overlap_budget_exceeded", "Too many overlapping collector queries"
                )
            overlap_end = min(end, previous["requestedEndEpochMs"])
            if snapshot(rows, times, begin, overlap_end) != snapshot(
                earlier, earlier_times, begin, overlap_end
            ):
                raise F12EvidenceValidationError(
                    "overlap_conflict", "Overlapping queries contain conflicting events"
                )
        active.append((summary, rows, times))


def _midnight_epoch_millis(day: date, zone: ZoneInfo) -> int:
    naive = datetime.combine(day, time.min)
    candidates = set()
    for fold in (0, 1):
        instant = naive.replace(tzinfo=zone, fold=fold).astimezone(UTC)
        if instant.astimezone(zone).replace(tzinfo=None) == naive:
            candidates.add(instant)
    if len(candidates) != 1:
        raise F12EvidenceValidationError(
            "invalid_local_day", "Local midnight is ambiguous or nonexistent"
        )
    return int(candidates.pop().timestamp() * 1000)


def unavailable_content_sha256() -> str:
    marker = f"modera-f12-unavailable-v1\0{F12_UNAVAILABLE_REASON}".encode("ascii")
    return hashlib.sha256(marker).hexdigest()


def expected_capture_id(
    profile_id: uuid.UUID,
    device_id: uuid.UUID,
    local_date: date,
    revision: int,
    content_sha256: str,
) -> str:
    value = "\0".join(
        (
            str(profile_id),
            str(device_id),
            local_date.isoformat(),
            str(revision),
            content_sha256,
        )
    ).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def validate_evidence(
    manifest: F12EvidenceManifest,
    uploaded_pairs: list[tuple[bytes, bytes]],
    *,
    profile_id: uuid.UUID,
    device_id: uuid.UUID,
    now: datetime | None = None,
) -> ValidatedF12Evidence:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    zone = ZoneInfo(manifest.time_zone)
    source_created_at = datetime.fromtimestamp(
        manifest.created_at_epoch_millis / 1000,
        tz=UTC,
    )
    if manifest.local_date >= source_created_at.astimezone(zone).date():
        raise F12EvidenceValidationError(
            "local_day_not_closed", "Evidence local_date must be closed at source creation"
        )
    if manifest.local_date >= now.astimezone(zone).date():
        raise F12EvidenceValidationError(
            "local_day_not_closed", "Evidence local_date must be closed at reception"
        )

    if manifest.evidence_state == "UNAVAILABLE":
        if uploaded_pairs:
            raise F12EvidenceValidationError(
                "unexpected_files", "UNAVAILABLE evidence cannot include export files"
            )
        content_sha256 = unavailable_content_sha256()
        if manifest.content_sha256 != content_sha256:
            raise F12EvidenceValidationError(
                "content_hash_mismatch", "UNAVAILABLE evidence sentinel SHA-256 mismatch"
            )
        return ValidatedF12Evidence(
            manifest=manifest,
            pairs=(),
            expected_capture_id=expected_capture_id(
                profile_id,
                device_id,
                manifest.local_date,
                manifest.revision,
                content_sha256,
            ),
            total_bytes=0,
            source_created_at=source_created_at,
        )

    if len(uploaded_pairs) != len(manifest.pairs):
        raise F12EvidenceValidationError(
            "file_count_mismatch", "Manifest pair count does not match uploaded files"
        )
    total_bytes = sum(len(summary) + len(events) for summary, events in uploaded_pairs)
    if total_bytes > F12_MAX_SOURCE_BYTES:
        raise F12EvidenceValidationError("evidence_too_large", "Evidence exceeds 32 MiB")

    pairs = tuple(
        F12EvidencePairBytes(
            collector_run_id=metadata.collector_run_id,
            summary_bytes=files[0],
            events_bytes=files[1],
            summary_sha256=metadata.summary_sha256,
            events_sha256=metadata.events_sha256,
        )
        for metadata, files in zip(manifest.pairs, uploaded_pairs, strict=True)
    )
    queries = []
    hardware = set()
    content_digest = hashlib.sha256()
    for pair in pairs:
        summary, rows, detected_hardware = _parse_pair(pair)
        queries.append((summary, rows))
        hardware.update(detected_hardware)
        content_digest.update(pair.summary_bytes)
        content_digest.update(b"\0")
        content_digest.update(pair.events_bytes)
        content_digest.update(b"\0")
    if len(hardware) > 1:
        raise F12EvidenceValidationError(
            "mixed_hardware", "Collector pairs contain different hardware metadata"
        )
    _validate_overlaps(queries)

    day_start = _midnight_epoch_millis(manifest.local_date, zone)
    day_end = _midnight_epoch_millis(manifest.local_date + timedelta(days=1), zone)
    if not any(
        summary["requestedBeginEpochMs"] == day_start
        and summary["requestedEndEpochMs"] == day_end
        for summary, _rows in queries
    ):
        raise F12EvidenceValidationError(
            "day_query_missing", "Evidence must include the complete declared local-day query"
        )
    if any(summary["collectedAtEpochMs"] > manifest.created_at_epoch_millis for summary, _ in queries):
        raise F12EvidenceValidationError(
            "invalid_capture_time", "Source creation predates a collector run"
        )

    content_sha256 = content_digest.hexdigest()
    if manifest.content_sha256 != content_sha256:
        raise F12EvidenceValidationError(
            "content_hash_mismatch", "Evidence content SHA-256 mismatch"
        )
    return ValidatedF12Evidence(
        manifest=manifest,
        pairs=pairs,
        expected_capture_id=expected_capture_id(
            profile_id,
            device_id,
            manifest.local_date,
            manifest.revision,
            content_sha256,
        ),
        total_bytes=total_bytes,
        source_created_at=source_created_at,
    )
