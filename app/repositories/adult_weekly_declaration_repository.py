from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdultWeeklyDeclaration, Device, Profile


class AdultWeeklyDeclarationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def lock_profile(self, profile_id: uuid.UUID) -> Profile | None:
        return await self.db.scalar(
            select(Profile)
            .where(Profile.id == profile_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def active_device(
        self,
        profile_id: uuid.UUID,
        device_id: uuid.UUID,
    ) -> Device | None:
        return await self.db.scalar(
            select(Device).where(
                Device.id == device_id,
                Device.profile_id == profile_id,
                Device.active.is_(True),
            )
        )

    async def next_revision(self, profile_id: uuid.UUID) -> int:
        latest = await self.db.scalar(
            select(func.max(AdultWeeklyDeclaration.revision)).where(
                AdultWeeklyDeclaration.profile_id == profile_id
            )
        )
        return int(latest or 0) + 1

    async def get_for_update(
        self,
        profile_id: uuid.UUID,
        declaration_id: uuid.UUID,
    ) -> AdultWeeklyDeclaration | None:
        return await self.db.scalar(
            select(AdultWeeklyDeclaration)
            .where(
                AdultWeeklyDeclaration.id == declaration_id,
                AdultWeeklyDeclaration.profile_id == profile_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def list_for_profile(
        self,
        profile_id: uuid.UUID,
    ) -> list[AdultWeeklyDeclaration]:
        query = (
            select(AdultWeeklyDeclaration)
            .where(AdultWeeklyDeclaration.profile_id == profile_id)
            .order_by(
                AdultWeeklyDeclaration.revision.desc(),
                AdultWeeklyDeclaration.created_at.desc(),
            )
        )
        return list((await self.db.scalars(query)).all())

    async def list_overlapping(
        self,
        profile_id: uuid.UUID,
        *,
        date_from: date,
        date_to: date,
    ) -> list[AdultWeeklyDeclaration]:
        query = (
            select(AdultWeeklyDeclaration)
            .where(
                AdultWeeklyDeclaration.profile_id == profile_id,
                AdultWeeklyDeclaration.effective_from <= date_to,
                or_(
                    AdultWeeklyDeclaration.effective_to.is_(None),
                    AdultWeeklyDeclaration.effective_to >= date_from,
                ),
                or_(
                    AdultWeeklyDeclaration.revoked_effective_date.is_(None),
                    AdultWeeklyDeclaration.revoked_effective_date > date_from,
                ),
            )
            .order_by(AdultWeeklyDeclaration.revision.asc())
        )
        return list((await self.db.scalars(query)).all())

    async def add(self, declaration: AdultWeeklyDeclaration) -> None:
        self.db.add(declaration)
        await self.db.flush()

    async def commit(self) -> None:
        await self.db.commit()

    async def rollback(self) -> None:
        await self.db.rollback()

    async def refresh(self, declaration: AdultWeeklyDeclaration) -> None:
        await self.db.refresh(declaration)
