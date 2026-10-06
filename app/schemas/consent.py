import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.models import (
    Consent,
    ConsentPurposeType,
    ConsentStatus,
    RepresentativeVerificationStatus,
)


class RepresentativeVerificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    profile_id: uuid.UUID
    status: RepresentativeVerificationStatus
    verified_at: datetime | None
    verified_by: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class ConsentGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    privacy_notice_version: str = Field(min_length=1, max_length=50)
    purposes: list[ConsentPurposeType] = Field(min_length=1)

    @field_validator("privacy_notice_version")
    @classmethod
    def validate_privacy_notice_version(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("privacy_notice_version must not be empty")
        return normalized

    @field_validator("purposes")
    @classmethod
    def validate_purposes(
        cls,
        value: list[ConsentPurposeType],
    ) -> list[ConsentPurposeType]:
        if len(value) != len(set(value)):
            raise ValueError("purposes must not contain duplicates")
        return value


class ConsentRead(BaseModel):
    id: uuid.UUID
    profile_id: uuid.UUID
    status: ConsentStatus
    privacy_notice_version: str
    purposes: list[ConsentPurposeType]
    granted_at: datetime
    revoked_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_entity(cls, consent: Consent) -> "ConsentRead":
        return cls(
            id=consent.id,
            profile_id=consent.profile_id,
            status=consent.status,
            privacy_notice_version=consent.privacy_notice_version,
            purposes=[item.purpose for item in consent.purposes],
            granted_at=consent.granted_at,
            revoked_at=consent.revoked_at,
            created_at=consent.created_at,
            updated_at=consent.updated_at,
        )
