import uuid
from datetime import datetime

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    AdultProfileLink,
    Consent,
    ConsentPurpose,
    ConsentPurposeType,
    ConsentStatus,
    Profile,
    RepresentativeVerification,
    RepresentativeVerificationStatus,
)


class ConsentRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, consent_id: uuid.UUID) -> Consent | None:
        return await self.db.scalar(
            select(Consent)
            .options(selectinload(Consent.purposes))
            .where(Consent.id == consent_id)
        )

    async def get_current_active(
        self,
        adult_id: uuid.UUID,
        profile_id: uuid.UUID,
        purpose: ConsentPurposeType,
    ) -> Consent | None:
        return await self.db.scalar(
            select(Consent)
            .join(ConsentPurpose)
            .options(selectinload(Consent.purposes))
            .where(
                Consent.adult_id == adult_id,
                Consent.profile_id == profile_id,
                Consent.status == ConsentStatus.ACTIVE,
                Consent.revoked_at.is_(None),
                ConsentPurpose.purpose == purpose,
            )
        )

    async def get_latest(
        self,
        adult_id: uuid.UUID,
        profile_id: uuid.UUID,
    ) -> Consent | None:
        return await self.db.scalar(
            select(Consent)
            .options(selectinload(Consent.purposes))
            .where(Consent.adult_id == adult_id, Consent.profile_id == profile_id)
            .order_by(Consent.granted_at.desc())
            .limit(1)
        )

    async def create(
        self,
        *,
        adult_id: uuid.UUID,
        profile_id: uuid.UUID,
        privacy_notice_version: str,
        purposes: list[ConsentPurposeType],
        granted_at: datetime,
    ) -> Consent:
        consent = Consent(
            adult_id=adult_id,
            profile_id=profile_id,
            status=ConsentStatus.ACTIVE,
            privacy_notice_version=privacy_notice_version,
            granted_at=granted_at,
        )
        consent.purposes = [ConsentPurpose(purpose=purpose) for purpose in purposes]
        self.db.add(consent)
        await self.db.flush()
        return consent

    async def revoke(self, consent: Consent, revoked_at: datetime) -> Consent:
        consent.status = ConsentStatus.REVOKED
        consent.revoked_at = revoked_at
        await self.db.flush()
        return consent

    async def has_active_usage_monitoring_authorization(
        self,
        profile_id: uuid.UUID,
    ) -> bool:
        authorization = (
            select(1)
            .select_from(AdultProfileLink)
            .join(
                RepresentativeVerification,
                (RepresentativeVerification.adult_id == AdultProfileLink.adult_id)
                & (RepresentativeVerification.profile_id == AdultProfileLink.profile_id),
            )
            .join(
                Consent,
                (Consent.adult_id == AdultProfileLink.adult_id)
                & (Consent.profile_id == AdultProfileLink.profile_id),
            )
            .join(ConsentPurpose, ConsentPurpose.consent_id == Consent.id)
            .where(
                AdultProfileLink.profile_id == profile_id,
                AdultProfileLink.active.is_(True),
                AdultProfileLink.revoked_at.is_(None),
                RepresentativeVerification.status
                == RepresentativeVerificationStatus.APPROVED,
                Consent.status == ConsentStatus.ACTIVE,
                Consent.revoked_at.is_(None),
                ConsentPurpose.purpose == ConsentPurposeType.USAGE_MONITORING,
            )
        )
        return bool(
            await self.db.scalar(
                select(
                    exists(authorization),
                ).where(
                    exists(
                        select(1).where(Profile.id == profile_id, Profile.active.is_(True))
                    )
                )
            )
        )
