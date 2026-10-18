import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    AppUsage,
    DailyUsage,
    DeviceDailyUsageHead,
    SnapshotAppUsage,
    UsageSnapshot,
)
from app.schemas.usage import DailyUsageCreate


class UsageRepository:
    def __init__(self, db: AsyncSession): self.db = db

    async def get_or_create_daily_for_update(self, profile_id: uuid.UUID, day: date) -> tuple[DailyUsage, bool]:
        query = select(DailyUsage).where(
            DailyUsage.profile_id == profile_id,
            DailyUsage.date == day,
        ).with_for_update()
        usage = (await self.db.execute(query)).scalar_one_or_none()
        if usage is not None:
            return usage, False

        created = False
        try:
            async with self.db.begin_nested():
                candidate = DailyUsage(profile_id=profile_id, date=day)
                self.db.add(candidate)
                await self.db.flush()
                created = True
        except IntegrityError:
            pass
        usage = (await self.db.execute(query)).scalar_one()
        return usage, created

    async def get_snapshot(self, snapshot_id: uuid.UUID) -> UsageSnapshot | None:
        return await self.db.scalar(
            select(UsageSnapshot)
            .options(selectinload(UsageSnapshot.applications))
            .where(UsageSnapshot.id == snapshot_id)
        )

    async def get_head(self, device_id: uuid.UUID, day: date) -> DeviceDailyUsageHead | None:
        return await self.db.scalar(
            select(DeviceDailyUsageHead)
            .where(
                DeviceDailyUsageHead.device_id == device_id,
                DeviceDailyUsageHead.date == day,
            )
            .with_for_update()
        )

    async def create_snapshot(
        self,
        profile_id: uuid.UUID,
        device_id: uuid.UUID,
        data: DailyUsageCreate,
        content_hash: str,
    ) -> UsageSnapshot:
        statuses = data.metric_statuses
        night_window = data.night_window
        snapshot = UsageSnapshot(
            id=data.snapshot_id,
            profile_id=profile_id,
            device_id=device_id,
            date=data.date,
            captured_at=data.captured_at.astimezone(timezone.utc),
            period_start_at=data.period_start_at.astimezone(timezone.utc),
            period_end_at=data.period_end_at.astimezone(timezone.utc),
            timezone_name=data.timezone,
            duration_unit=data.duration_unit,
            measurement_method=data.measurement_method,
            calculation_version=data.calculation_version,
            coverage_status=data.coverage_status.value,
            total_usage_minutes=data.total_usage_minutes,
            session_count=data.session_count,
            night_usage_minutes=data.night_usage_minutes,
            last_night_activity_at=(
                data.last_night_activity_at.astimezone(timezone.utc)
                if data.last_night_activity_at is not None
                else None
            ),
            max_session_minutes=data.max_session_minutes,
            unlock_count=data.unlock_count,
            total_usage_status=statuses.total_usage_minutes.value,
            session_count_status=statuses.session_count.value,
            night_usage_status=statuses.night_usage_minutes.value,
            last_night_activity_status=statuses.last_night_activity_at.value,
            max_session_status=statuses.max_session_minutes.value,
            unlock_count_status=statuses.unlock_count.value,
            applications_status=statuses.applications.value,
            applications_present=data.applications is not None,
            night_window_start=night_window.start_local_time if night_window else None,
            night_window_end=night_window.end_local_time if night_window else None,
            content_hash=content_hash,
        )
        snapshot.applications = [
            SnapshotAppUsage(**item.model_dump()) for item in (data.applications or [])
        ]
        self.db.add(snapshot)
        await self.db.flush()
        return snapshot

    async def advance_head(
        self,
        head: DeviceDailyUsageHead | None,
        *,
        profile_id: uuid.UUID,
        device_id: uuid.UUID,
        day: date,
        snapshot_id: uuid.UUID,
    ) -> None:
        if head is None:
            self.db.add(
                DeviceDailyUsageHead(
                    profile_id=profile_id,
                    device_id=device_id,
                    date=day,
                    snapshot_id=snapshot_id,
                )
            )
        else:
            head.snapshot_id = snapshot_id
            head.updated_at = datetime.now(timezone.utc)
        await self.db.flush()

    async def list_current_snapshots(self, profile_id: uuid.UUID, day: date) -> list[UsageSnapshot]:
        query = (
            select(UsageSnapshot)
            .join(DeviceDailyUsageHead, DeviceDailyUsageHead.snapshot_id == UsageSnapshot.id)
            .options(selectinload(UsageSnapshot.applications))
            .where(
                DeviceDailyUsageHead.profile_id == profile_id,
                DeviceDailyUsageHead.date == day,
            )
            .order_by(UsageSnapshot.device_id.asc())
        )
        return list((await self.db.execute(query)).scalars().all())

    async def replace_consolidated_applications(
        self,
        usage: DailyUsage,
        applications: list[dict] | None,
    ) -> None:
        usage.applications.clear()
        if applications is not None:
            usage.applications.extend(AppUsage(**item) for item in applications)
        await self.db.flush()

    async def get_by_date(self, profile_id: uuid.UUID, day: date) -> DailyUsage | None:
        query = select(DailyUsage).options(selectinload(DailyUsage.applications)).where(DailyUsage.profile_id == profile_id, DailyUsage.date == day)
        return (await self.db.execute(query)).scalar_one_or_none()

    async def list(
        self,
        profile_id: uuid.UUID,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[DailyUsage]:
        query = select(DailyUsage).options(selectinload(DailyUsage.applications)).where(DailyUsage.profile_id == profile_id)
        if date_from is not None:
            query = query.where(DailyUsage.date >= date_from)
        if date_to is not None:
            query = query.where(DailyUsage.date <= date_to)
        query = query.order_by(DailyUsage.date.desc())
        return list((await self.db.execute(query)).scalars().all())

