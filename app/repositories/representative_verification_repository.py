import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    RepresentativeVerification,
    RepresentativeVerificationStatus,
)


class RepresentativeVerificationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_adult_profile(
        self,
        adult_id: uuid.UUID,
        profile_id: uuid.UUID,
    ) -> RepresentativeVerification | None:
        return await self.db.scalar(
            select(RepresentativeVerification).where(
                RepresentativeVerification.adult_id == adult_id,
                RepresentativeVerification.profile_id == profile_id,
            )
        )

    async def get_by_adult_profile_for_update(
        self,
        adult_id: uuid.UUID,
        profile_id: uuid.UUID,
    ) -> RepresentativeVerification | None:
        return await self.db.scalar(
            select(RepresentativeVerification).where(
                RepresentativeVerification.adult_id == adult_id,
                RepresentativeVerification.profile_id == profile_id,
            ).with_for_update()
        )

    async def create_pending(
        self,
        adult_id: uuid.UUID,
        profile_id: uuid.UUID,
    ) -> RepresentativeVerification:
        verification = RepresentativeVerification(
            adult_id=adult_id,
            profile_id=profile_id,
            status=RepresentativeVerificationStatus.PENDING,
        )
        self.db.add(verification)
        await self.db.flush()
        return verification

    async def set_pending(
        self,
        verification: RepresentativeVerification,
    ) -> RepresentativeVerification:
        verification.status = RepresentativeVerificationStatus.PENDING
        verification.verified_at = None
        verification.verified_by = None
        verification.notes = None
        await self.db.flush()
        return verification

    async def approve(
        self,
        verification: RepresentativeVerification,
        *,
        verified_at: datetime,
        verified_by: str,
        notes: str | None,
    ) -> RepresentativeVerification:
        verification.status = RepresentativeVerificationStatus.APPROVED
        verification.verified_at = verified_at
        verification.verified_by = verified_by
        verification.notes = notes
        await self.db.flush()
        return verification

    async def reject(
        self,
        verification: RepresentativeVerification,
        *,
        verified_at: datetime,
        verified_by: str,
        notes: str | None,
    ) -> RepresentativeVerification:
        verification.status = RepresentativeVerificationStatus.REJECTED
        verification.verified_at = verified_at
        verification.verified_by = verified_by
        verification.notes = notes
        await self.db.flush()
        return verification
