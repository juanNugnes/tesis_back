import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AdultAccount,
    AuditActorType,
    AuditEventType,
    Consent,
    ConsentPurposeType,
    ConsentStatus,
    Profile,
    RepresentativeVerificationStatus,
)
from app.repositories.adult_profile_link_repository import AdultProfileLinkRepository
from app.repositories.audit_event_repository import AuditEventRepository
from app.repositories.consent_repository import ConsentRepository
from app.repositories.representative_verification_repository import (
    RepresentativeVerificationRepository,
)


class ConsentNotAllowedError(Exception):
    pass


class ConsentNotFoundError(Exception):
    pass


class ActiveConsentConflictError(Exception):
    pass


@dataclass(frozen=True)
class ConsentGrantResult:
    consent: Consent
    created: bool


class ConsentService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.consents = ConsentRepository(db)
        self.verifications = RepresentativeVerificationRepository(db)
        self.links = AdultProfileLinkRepository(db)
        self.audit = AuditEventRepository(db)

    async def grant(
        self,
        adult: AdultAccount,
        profile: Profile,
        *,
        privacy_notice_version: str,
        purposes: list[ConsentPurposeType],
    ) -> ConsentGrantResult:
        if not profile.active:
            raise ConsentNotAllowedError
        if await self.links.get_active_link(adult.id, profile.id) is None:
            raise ConsentNotAllowedError
        verification = await self.verifications.get_by_adult_profile(adult.id, profile.id)
        if (
            verification is None
            or verification.status != RepresentativeVerificationStatus.APPROVED
        ):
            raise ConsentNotAllowedError

        purpose = ConsentPurposeType.USAGE_MONITORING
        current = await self.consents.get_current_active(adult.id, profile.id, purpose)
        if current is not None:
            current_purposes = {item.purpose for item in current.purposes}
            if (
                current.privacy_notice_version == privacy_notice_version
                and current_purposes == set(purposes)
            ):
                return ConsentGrantResult(current, created=False)
            raise ActiveConsentConflictError

        try:
            consent = await self.consents.create(
                adult_id=adult.id,
                profile_id=profile.id,
                privacy_notice_version=privacy_notice_version,
                purposes=purposes,
                granted_at=datetime.now(timezone.utc),
            )
            await self.audit.append(
                actor_type=AuditActorType.ADULT,
                actor_id=adult.id,
                profile_id=profile.id,
                event_type=AuditEventType.CONSENT_GRANTED,
                metadata={
                    "consent_id": str(consent.id),
                    "privacy_notice_version": privacy_notice_version,
                    "purposes": [purpose.value for purpose in purposes],
                },
            )
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            current = await self.consents.get_current_active(adult.id, profile.id, purpose)
            if current is not None:
                current_purposes = {item.purpose for item in current.purposes}
                if (
                    current.privacy_notice_version == privacy_notice_version
                    and current_purposes == set(purposes)
                ):
                    return ConsentGrantResult(current, created=False)
            raise ActiveConsentConflictError
        return ConsentGrantResult(consent, created=True)

    async def get_current(self, adult: AdultAccount, profile: Profile) -> Consent:
        consent = await self.consents.get_latest(adult.id, profile.id)
        if consent is None:
            raise ConsentNotFoundError
        return consent

    async def revoke(
        self,
        adult: AdultAccount,
        profile: Profile,
        consent_id: uuid.UUID,
    ) -> Consent:
        if await self.links.get_active_link(adult.id, profile.id) is None:
            raise ConsentNotFoundError
        consent = await self.consents.get(consent_id)
        if (
            consent is None
            or consent.adult_id != adult.id
            or consent.profile_id != profile.id
        ):
            raise ConsentNotFoundError
        if consent.status == ConsentStatus.REVOKED:
            return consent

        consent = await self.consents.revoke(consent, datetime.now(timezone.utc))
        await self.audit.append(
            actor_type=AuditActorType.ADULT,
            actor_id=adult.id,
            profile_id=profile.id,
            event_type=AuditEventType.CONSENT_REVOKED,
            metadata={"consent_id": str(consent.id)},
        )
        await self.db.commit()
        return consent
