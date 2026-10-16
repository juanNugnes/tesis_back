from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import uuid
import weakref
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable

from sqlalchemy.exc import IntegrityError

from app.db.models import (
    DailyUsage,
    UsageCoverageStatus as DbCoverageStatus,
    UsageMetricStatus as DbMetricStatus,
    UsageSnapshot,
)
from app.repositories.usage_repository import UsageRepository
from app.schemas.usage import (
    AppUsageRead,
    DailyUsageCreate,
    DailyUsageRead,
    NightWindowDefinition,
    UsageCoverageStatus,
    UsageMetricStatus,
    UsageMetricStatuses,
    UsageSourceRead,
    UsageWriteResult,
)


class UsageConflictError(Exception):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(detail)


class _UsagePeriodLockRegistry:
    _guard = threading.Lock()
    _locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

    @classmethod
    def get(cls, key: str) -> asyncio.Lock:
        with cls._guard:
            lock = cls._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                cls._locks[key] = lock
            return lock


@dataclass(frozen=True)
class ConsolidatedMetric:
    value: int | None
    status: DbMetricStatus


def _enum_value(value):
    return value.value if hasattr(value, "value") else value


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class UsageService:
    def __init__(self, repository: UsageRepository):
        self.repository = repository

    @staticmethod
    def _content_hash(data: DailyUsageCreate) -> str:
        canonical = data.model_dump(mode="json")
        for field in ("captured_at", "period_start_at", "period_end_at"):
            canonical[field] = _as_utc(getattr(data, field)).isoformat()
        encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    async def ingest(
        self,
        profile_id: uuid.UUID,
        device_id: uuid.UUID,
        data: DailyUsageCreate,
    ) -> UsageWriteResult:
        lock = _UsagePeriodLockRegistry.get(f"{profile_id}:{data.date.isoformat()}")
        async with lock:
            return await self._ingest_locked(profile_id, device_id, data)

    async def _ingest_locked(
        self,
        profile_id: uuid.UUID,
        device_id: uuid.UUID,
        data: DailyUsageCreate,
    ) -> UsageWriteResult:
        content_hash = self._content_hash(data)
        try:
            daily, daily_created = await self.repository.get_or_create_daily_for_update(
                profile_id,
                data.date,
            )
            existing = await self.repository.get_snapshot(data.snapshot_id)
            if existing is not None:
                same_owner = (
                    existing.profile_id == profile_id
                    and existing.device_id == device_id
                    and existing.date == data.date
                )
                if not same_owner or existing.content_hash != content_hash:
                    raise UsageConflictError(
                        "snapshot_conflict",
                        "snapshot_id was already used with different content or ownership",
                    )
                await self.repository.db.commit()
                return UsageWriteResult(
                    result="unchanged",
                    usage=await self.get(profile_id, data.date),
                )

            head = await self.repository.get_head(device_id, data.date)
            if head is not None:
                current = await self.repository.get_snapshot(head.snapshot_id)
                if current is None:
                    raise RuntimeError("usage head references a missing snapshot")
                incoming_captured_at = _as_utc(data.captured_at)
                current_captured_at = _as_utc(current.captured_at)
                if incoming_captured_at == current_captured_at:
                    raise UsageConflictError(
                        "ambiguous_order",
                        "a different snapshot already exists with the same captured_at",
                    )
                if incoming_captured_at < current_captured_at:
                    raise UsageConflictError(
                        "stale_snapshot",
                        "an older snapshot cannot replace the current device snapshot",
                    )

            snapshot = await self.repository.create_snapshot(
                profile_id,
                device_id,
                data,
                content_hash,
            )
            await self.repository.advance_head(
                head,
                profile_id=profile_id,
                device_id=device_id,
                day=data.date,
                snapshot_id=snapshot.id,
            )
            snapshots = await self.repository.list_current_snapshots(profile_id, data.date)
            await self._consolidate(daily, snapshots)
            await self.repository.db.commit()
            return UsageWriteResult(
                result="created" if daily_created else "updated",
                usage=await self.get(profile_id, data.date),
            )
        except UsageConflictError:
            await self.repository.db.rollback()
            raise
        except IntegrityError as exc:
            await self.repository.db.rollback()
            existing = await self.repository.get_snapshot(data.snapshot_id)
            if (
                existing is not None
                and existing.profile_id == profile_id
                and existing.device_id == device_id
                and existing.date == data.date
                and existing.content_hash == content_hash
            ):
                return UsageWriteResult(
                    result="unchanged",
                    usage=await self.get(profile_id, data.date),
                )
            raise UsageConflictError(
                "snapshot_conflict",
                "snapshot_id was concurrently used with different content or ownership",
            ) from exc

    @staticmethod
    def _merge_metric(
        snapshots: list[UsageSnapshot],
        value_attribute: str,
        status_attribute: str,
        aggregate: Callable[[list[int]], int],
    ) -> ConsolidatedMetric:
        values = [getattr(item, value_attribute) for item in snapshots]
        present_values = [value for value in values if value is not None]
        statuses = [DbMetricStatus(_enum_value(getattr(item, status_attribute))) for item in snapshots]
        if statuses and all(status == DbMetricStatus.MEASURED for status in statuses):
            status = DbMetricStatus.MEASURED
        elif present_values:
            status = DbMetricStatus.INCOMPLETE
        elif statuses and all(status == statuses[0] for status in statuses):
            status = statuses[0]
        else:
            status = DbMetricStatus.UNAVAILABLE
        return ConsolidatedMetric(
            value=aggregate(present_values) if present_values else None,
            status=status,
        )

    async def _consolidate(self, daily: DailyUsage, snapshots: list[UsageSnapshot]) -> None:
        sums = (
            ("total_usage_minutes", "total_usage_status"),
            ("session_count", "session_count_status"),
            ("night_usage_minutes", "night_usage_status"),
            ("unlock_count", "unlock_count_status"),
        )
        for value_attribute, status_attribute in sums:
            merged = self._merge_metric(snapshots, value_attribute, status_attribute, sum)
            setattr(daily, value_attribute, merged.value)
            setattr(daily, status_attribute, merged.status)
        maximum = self._merge_metric(
            snapshots,
            "max_session_minutes",
            "max_session_status",
            max,
        )
        daily.max_session_minutes = maximum.value
        daily.max_session_status = maximum.status

        application_statuses = [
            DbMetricStatus(_enum_value(item.applications_status)) for item in snapshots
        ]
        daily.applications_present = any(item.applications_present for item in snapshots)
        if application_statuses and all(
            status == DbMetricStatus.MEASURED for status in application_statuses
        ):
            daily.applications_status = DbMetricStatus.MEASURED
        elif daily.applications_present:
            daily.applications_status = DbMetricStatus.INCOMPLETE
        elif application_statuses and all(status == application_statuses[0] for status in application_statuses):
            daily.applications_status = application_statuses[0]
        else:
            daily.applications_status = DbMetricStatus.UNAVAILABLE

        applications: dict[str, dict] = {}
        for snapshot in sorted(snapshots, key=lambda item: _as_utc(item.captured_at)):
            if not snapshot.applications_present:
                continue
            for app in snapshot.applications:
                aggregate = applications.setdefault(
                    app.package_name,
                    {
                        "package_name": app.package_name,
                        "app_name": app.app_name,
                        "category": None,
                        "usage_minutes": 0,
                        "_categories": set(),
                    },
                )
                aggregate["usage_minutes"] += app.usage_minutes
                if app.app_name is not None:
                    aggregate["app_name"] = app.app_name
                if app.category is not None:
                    aggregate["_categories"].add(app.category)
        for aggregate in applications.values():
            categories = aggregate.pop("_categories")
            aggregate["category"] = next(iter(categories)) if len(categories) == 1 else None
        await self.repository.replace_consolidated_applications(
            daily,
            list(applications.values()) if daily.applications_present else None,
        )

        period_signatures = {
            (_as_utc(item.period_start_at), _as_utc(item.period_end_at), item.timezone_name)
            for item in snapshots
        }
        daily.periods_uniform = len(period_signatures) == 1
        if daily.periods_uniform:
            daily.period_start_at, daily.period_end_at, daily.timezone_name = next(
                iter(period_signatures)
            )
        else:
            daily.period_start_at = None
            daily.period_end_at = None
            daily.timezone_name = None

        method_signatures = {
            (item.measurement_method, item.calculation_version) for item in snapshots
        }
        if len(method_signatures) == 1:
            daily.measurement_method, daily.calculation_version = next(iter(method_signatures))
        else:
            daily.measurement_method = None
            daily.calculation_version = None

        night_signatures = {
            (item.night_window_start, item.night_window_end) for item in snapshots
        }
        if len(night_signatures) == 1:
            daily.night_window_start, daily.night_window_end = next(iter(night_signatures))
        else:
            daily.night_window_start = None
            daily.night_window_end = None
        daily.duration_unit = "MINUTES"
        daily.coverage_status = (
            DbCoverageStatus.PARTIAL
            if any(_enum_value(item.coverage_status) == DbCoverageStatus.PARTIAL.value for item in snapshots)
            else DbCoverageStatus.UNKNOWN
        )
        daily.source_count = len(snapshots)
        daily.updated_at = datetime.now(timezone.utc)

    async def get(self, profile_id: uuid.UUID, day: date) -> DailyUsageRead:
        daily = await self.repository.get_by_date(profile_id, day)
        if daily is None:
            raise LookupError
        snapshots = await self.repository.list_current_snapshots(profile_id, day)
        return self._to_read(daily, snapshots)

    async def list(
        self,
        profile_id: uuid.UUID,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[DailyUsageRead]:
        daily_rows = await self.repository.list(
            profile_id,
            date_from=date_from,
            date_to=date_to,
        )
        return [
            self._to_read(
                daily,
                await self.repository.list_current_snapshots(profile_id, daily.date),
            )
            for daily in daily_rows
        ]

    @staticmethod
    def _statuses_from_model(item) -> UsageMetricStatuses:
        return UsageMetricStatuses(
            total_usage_minutes=UsageMetricStatus(_enum_value(item.total_usage_status)),
            session_count=UsageMetricStatus(_enum_value(item.session_count_status)),
            night_usage_minutes=UsageMetricStatus(_enum_value(item.night_usage_status)),
            max_session_minutes=UsageMetricStatus(_enum_value(item.max_session_status)),
            unlock_count=UsageMetricStatus(_enum_value(item.unlock_count_status)),
            applications=UsageMetricStatus(_enum_value(item.applications_status)),
        )

    @classmethod
    def _source_read(cls, snapshot: UsageSnapshot) -> UsageSourceRead:
        night_window = None
        if snapshot.night_window_start is not None and snapshot.night_window_end is not None:
            night_window = NightWindowDefinition(
                source="PROFILE_SLEEP_SCHEDULE",
                start_local_time=snapshot.night_window_start,
                end_local_time=snapshot.night_window_end,
            )
        return UsageSourceRead(
            snapshot_id=snapshot.id,
            device_id=snapshot.device_id,
            captured_at=_as_utc(snapshot.captured_at),
            period_start_at=_as_utc(snapshot.period_start_at),
            period_end_at=_as_utc(snapshot.period_end_at),
            timezone=snapshot.timezone_name,
            duration_unit="MINUTES",
            measurement_method="ANDROID_USAGE_STATS_AGGREGATES",
            calculation_version=snapshot.calculation_version,
            coverage_status=UsageCoverageStatus(_enum_value(snapshot.coverage_status)),
            total_usage_minutes=snapshot.total_usage_minutes,
            session_count=snapshot.session_count,
            night_usage_minutes=snapshot.night_usage_minutes,
            max_session_minutes=snapshot.max_session_minutes,
            unlock_count=snapshot.unlock_count,
            applications=(
                [
                    {
                        "package_name": item.package_name,
                        "app_name": item.app_name,
                        "category": item.category,
                        "usage_minutes": item.usage_minutes,
                    }
                    for item in snapshot.applications
                ]
                if snapshot.applications_present
                else None
            ),
            metric_statuses=cls._statuses_from_model(snapshot),
            night_window=night_window,
        )

    @classmethod
    def _to_read(cls, daily: DailyUsage, snapshots: list[UsageSnapshot]) -> DailyUsageRead:
        night_window = None
        if daily.night_window_start is not None and daily.night_window_end is not None:
            night_window = NightWindowDefinition(
                source="PROFILE_SLEEP_SCHEDULE",
                start_local_time=daily.night_window_start,
                end_local_time=daily.night_window_end,
            )
        applications = None
        if daily.applications_present:
            applications = [AppUsageRead.model_validate(item) for item in daily.applications]
        return DailyUsageRead(
            id=daily.id,
            profile_id=daily.profile_id,
            date=daily.date,
            total_usage_minutes=daily.total_usage_minutes,
            session_count=daily.session_count,
            night_usage_minutes=daily.night_usage_minutes,
            max_session_minutes=daily.max_session_minutes,
            unlock_count=daily.unlock_count,
            applications=applications,
            metric_statuses=cls._statuses_from_model(daily),
            coverage_status=UsageCoverageStatus(_enum_value(daily.coverage_status)),
            period_start_at=_as_utc(daily.period_start_at) if daily.period_start_at else None,
            period_end_at=_as_utc(daily.period_end_at) if daily.period_end_at else None,
            timezone=daily.timezone_name,
            duration_unit="MINUTES",
            measurement_method=daily.measurement_method,
            calculation_version=daily.calculation_version,
            night_window=night_window,
            periods_uniform=daily.periods_uniform,
            source_count=daily.source_count,
            sources=[cls._source_read(item) for item in snapshots],
            created_at=_as_utc(daily.created_at),
            updated_at=_as_utc(daily.updated_at),
        )
