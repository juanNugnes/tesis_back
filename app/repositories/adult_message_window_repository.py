from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    AdultWeeklyDeclaration,
    AutomaticOfferSettings,
    Device,
    F12EvidencePreparation,
    F12RawEvidenceCapture,
    Profile,
    ProfileContext,
)


class AdultMessageWindowRepository:
    """Read-only queries used to diagnose a possible real weekly window."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def profile_exists(self, profile_id: uuid.UUID) -> bool:
        return (
            await self.db.scalar(select(Profile.id).where(Profile.id == profile_id))
        ) is not None

    async def list_evidence(
        self,
        profile_id: uuid.UUID,
        *,
        date_from: date,
        date_to: date,
    ) -> list[F12RawEvidenceCapture]:
        query = (
            select(F12RawEvidenceCapture)
            .options(
                selectinload(F12RawEvidenceCapture.preparation).selectinload(
                    F12EvidencePreparation.historical_submission
                )
            )
            .where(
                F12RawEvidenceCapture.profile_id == profile_id,
                F12RawEvidenceCapture.local_date >= date_from,
                F12RawEvidenceCapture.local_date <= date_to,
            )
            .order_by(
                F12RawEvidenceCapture.local_date.asc(),
                F12RawEvidenceCapture.revision.asc(),
            )
        )
        return list((await self.db.scalars(query)).all())

    async def list_contexts(
        self,
        profile_id: uuid.UUID,
        *,
        date_from: date,
        date_to: date,
    ) -> list[ProfileContext]:
        query = (
            select(ProfileContext)
            .where(
                ProfileContext.profile_id == profile_id,
                ProfileContext.start_date <= date_to,
                (
                    ProfileContext.end_date.is_(None)
                    | (ProfileContext.end_date >= date_from)
                ),
            )
            .order_by(ProfileContext.start_date.asc(), ProfileContext.id.asc())
        )
        return list((await self.db.scalars(query)).all())

    async def list_weekly_declarations(
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
                (
                    AdultWeeklyDeclaration.effective_to.is_(None)
                    | (AdultWeeklyDeclaration.effective_to >= date_from)
                ),
                (
                    AdultWeeklyDeclaration.revoked_effective_date.is_(None)
                    | (AdultWeeklyDeclaration.revoked_effective_date > date_from)
                ),
            )
            .order_by(AdultWeeklyDeclaration.revision.asc())
        )
        return list((await self.db.scalars(query)).all())

    async def has_current_shared_phone_setting(self, profile_id: uuid.UUID) -> bool:
        return (
            await self.db.scalar(
                select(AutomaticOfferSettings.profile_id).where(
                    AutomaticOfferSettings.profile_id == profile_id
                )
            )
        ) is not None

    async def current_device_is_linked(
        self,
        profile_id: uuid.UUID,
        device_id: uuid.UUID | None,
    ) -> bool:
        if device_id is None:
            return False
        return (
            await self.db.scalar(
                select(Device.id).where(
                    Device.id == device_id,
                    Device.profile_id == profile_id,
                )
            )
        ) is not None
