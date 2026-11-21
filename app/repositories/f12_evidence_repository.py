import uuid
from datetime import date, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Device, F12RawEvidenceCapture


class F12EvidenceRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def lock_device(self, device_id: uuid.UUID) -> Device | None:
        return await self.db.scalar(
            select(Device)
            .where(Device.id == device_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def get_by_capture_id(
        self,
        capture_id: str,
    ) -> F12RawEvidenceCapture | None:
        return await self.db.scalar(
            select(F12RawEvidenceCapture)
            .options(selectinload(F12RawEvidenceCapture.pairs))
            .where(F12RawEvidenceCapture.capture_id == capture_id)
        )

    async def get_by_revision(
        self,
        *,
        profile_id: uuid.UUID,
        device_id: uuid.UUID,
        local_date: date,
        revision: int,
    ) -> F12RawEvidenceCapture | None:
        return await self.db.scalar(
            select(F12RawEvidenceCapture)
            .options(selectinload(F12RawEvidenceCapture.pairs))
            .where(
                F12RawEvidenceCapture.profile_id == profile_id,
                F12RawEvidenceCapture.device_id == device_id,
                F12RawEvidenceCapture.local_date == local_date,
                F12RawEvidenceCapture.revision == revision,
            )
        )

    async def latest_revision(
        self,
        *,
        profile_id: uuid.UUID,
        device_id: uuid.UUID,
        local_date: date,
    ) -> int | None:
        return await self.db.scalar(
            select(F12RawEvidenceCapture.revision)
            .where(
                F12RawEvidenceCapture.profile_id == profile_id,
                F12RawEvidenceCapture.device_id == device_id,
                F12RawEvidenceCapture.local_date == local_date,
            )
            .order_by(F12RawEvidenceCapture.revision.desc())
            .limit(1)
        )

    async def purge_expired(self, *, profile_id: uuid.UUID, now: datetime) -> None:
        await self.db.execute(
            delete(F12RawEvidenceCapture).where(
                F12RawEvidenceCapture.profile_id == profile_id,
                F12RawEvidenceCapture.expires_at.is_not(None),
                F12RawEvidenceCapture.expires_at <= now,
            )
        )

    async def add(self, capture: F12RawEvidenceCapture) -> None:
        self.db.add(capture)
        await self.db.flush()

    async def commit(self) -> None:
        await self.db.commit()

    async def rollback(self) -> None:
        await self.db.rollback()
