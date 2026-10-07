import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AuditActorType,
    AuditEventType,
    RepresentativeVerification,
    RepresentativeVerificationStatus,
)
from app.repositories.adult_profile_link_repository import AdultProfileLinkRepository
from app.repositories.audit_event_repository import AuditEventRepository
from app.repositories.representative_verification_repository import (
    RepresentativeVerificationRepository,
)


class RepresentativeVerificationNotFoundError(Exception):
    pass


class RepresentativeLinkNotFoundError(Exception):
    pass


class RepresentativeVerificationService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.verifications = RepresentativeVerificationRepository(db)
        self.links = AdultProfileLinkRepository(db)
        self.audit = AuditEventRepository(db)

    async def get(
        self,
        adult_id: uuid.UUID,
        profile_id: uuid.UUID,
    ) -> RepresentativeVerification:
        verification = await self.verifications.get_by_adult_profile(adult_id, profile_id)
        if verification is None:
            raise RepresentativeVerificationNotFoundError
        return verification

    async def ensure_pending(
        self,
        adult_id: uuid.UUID,
        profile_id: uuid.UUID,
        *,
        commit: bool = True,
    ) -> RepresentativeVerification:
        if await self.links.get_link(adult_id, profile_id) is None:
            raise RepresentativeLinkNotFoundError
        verification = await self.verifications.get_by_adult_profile(adult_id, profile_id)
        if verification is None:
            verification = await self.verifications.create_pending(adult_id, profile_id)
        if commit:
            await self.db.commit()
        return verification

    async def set_pending(
        self,
        adult_id: uuid.UUID,
        profile_id: uuid.UUID,
    ) -> RepresentativeVerification:
        verification = await self.ensure_pending(adult_id, profile_id, commit=False)
        if verification.status != RepresentativeVerificationStatus.PENDING:
            verification = await self.verifications.set_pending(verification)
        await self.db.commit()
        return verification

    async def approve(
        self,
        adult_id: uuid.UUID,
        profile_id: uuid.UUID,
        *,
        verified_by: str,
        notes: str | None = None,
    ) -> RepresentativeVerification:
        return await self._decide(
            adult_id,
            profile_id,
            status=RepresentativeVerificationStatus.APPROVED,
            verified_by=verified_by,
            notes=notes,
        )

    async def approve_automatically(
        self,
        adult_id: uuid.UUID,
        profile_id: uuid.UUID,
        *,
        commit: bool = True,
    ) -> RepresentativeVerification:
        verification = await self.ensure_pending(adult_id, profile_id, commit=False)
        if verification.status in {
            RepresentativeVerificationStatus.APPROVED,
            RepresentativeVerificationStatus.REJECTED,
        }:
            if commit:
                await self.db.commit()
            return verification

        now = datetime.now(timezone.utc)
        verification = await self.verifications.approve(
            verification,
            verified_at=now,
            verified_by="automatic-mvp",
            notes=None,
        )
        await self.audit.append(
            actor_type=AuditActorType.INTERNAL,
            actor_id=None,
            profile_id=profile_id,
            event_type=AuditEventType.REPRESENTATIVE_VERIFICATION_APPROVED,
            metadata={"verified_by": "automatic-mvp"},
        )
        if commit:
            await self.db.commit()
        return verification

    async def reject(
        self,
        adult_id: uuid.UUID,
        profile_id: uuid.UUID,
        *,
        verified_by: str,
        notes: str | None = None,
    ) -> RepresentativeVerification:
        return await self._decide(
            adult_id,
            profile_id,
            status=RepresentativeVerificationStatus.REJECTED,
            verified_by=verified_by,
            notes=notes,
        )

    async def _decide(
        self,
        adult_id: uuid.UUID,
        profile_id: uuid.UUID,
        *,
        status: RepresentativeVerificationStatus,
        verified_by: str,
        notes: str | None,
    ) -> RepresentativeVerification:
        operator = verified_by.strip()
        if not operator or len(operator) > 100:
            raise ValueError("verified_by must contain between 1 and 100 characters")
        if notes is not None and len(notes) > 500:
            raise ValueError("notes must contain at most 500 characters")

        verification = await self.ensure_pending(adult_id, profile_id, commit=False)
        if verification.status == status:
            await self.db.commit()
            return verification

        now = datetime.now(timezone.utc)
        if status == RepresentativeVerificationStatus.APPROVED:
            verification = await self.verifications.approve(
                verification,
                verified_at=now,
                verified_by=operator,
                notes=notes,
            )
            event_type = AuditEventType.REPRESENTATIVE_VERIFICATION_APPROVED
        else:
            verification = await self.verifications.reject(
                verification,
                verified_at=now,
                verified_by=operator,
                notes=notes,
            )
            event_type = AuditEventType.REPRESENTATIVE_VERIFICATION_REJECTED

        await self.audit.append(
            actor_type=AuditActorType.INTERNAL,
            actor_id=None,
            profile_id=profile_id,
            event_type=event_type,
            metadata={"verified_by": operator},
        )
        await self.db.commit()
        return verification
