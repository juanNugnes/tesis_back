import enum
import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, Enum, Float, ForeignKey, Index, Integer, JSON, String, Text, Time, UniqueConstraint, false, text, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RiskLevel(str, enum.Enum):
    NORMAL = "NORMAL"
    ATTENTION = "ATTENTION"
    RELEVANT_CHANGE = "RELEVANT_CHANGE"


class Avatar(str, enum.Enum):
    fox = "fox"
    frog = "frog"
    dog = "dog"
    cat = "cat"
    penguin = "penguin"
    lion = "lion"
    koala = "koala"
    butterfly = "butterfly"


class ProfileContextType(str, enum.Enum):
    VACATION = "VACATION"
    ILLNESS = "ILLNESS"
    SCHOOL_ACTIVITY = "SCHOOL_ACTIVITY"
    ROUTINE_CHANGE = "ROUTINE_CHANGE"
    OTHER = "OTHER"


class ActivityInterest(str, enum.Enum):
    DRAWING_CREATING = "DRAWING_CREATING"
    READING_STORIES = "READING_STORIES"
    MUSIC = "MUSIC"
    GAMES_CHALLENGES = "GAMES_CHALLENGES"
    OUTDOORS = "OUTDOORS"
    MOVEMENT = "MOVEMENT"
    BUILDING = "BUILDING"
    WRITING = "WRITING"
    IMAGINATIVE_PLAY = "IMAGINATIVE_PLAY"


class AdaptationType(str, enum.Enum):
    NONE = "NONE"
    TEMPORARY = "TEMPORARY"
    PERMANENT = "PERMANENT"


class ActivityConsiderationType(str, enum.Enum):
    UPPER_BODY_MOBILITY = "UPPER_BODY_MOBILITY"
    LOWER_BODY_MOBILITY = "LOWER_BODY_MOBILITY"
    VISUAL = "VISUAL"
    AUDITORY = "AUDITORY"
    SOUND_SENSITIVITY = "SOUND_SENSITIVITY"


class ConsiderationDurationType(str, enum.Enum):
    TEMPORARY = "TEMPORARY"
    PERMANENT = "PERMANENT"


class FunctionalConsideration(str, enum.Enum):
    MOBILITY = "MOBILITY"
    VISION = "VISION"
    HEARING = "HEARING"
    ATTENTION_CONCENTRATION = "ATTENTION_CONCENTRATION"
    ARMS_HANDS = "ARMS_HANDS"
    LEGS = "LEGS"
    COMMUNICATION = "COMMUNICATION"
    SENSORY_SENSITIVITY = "SENSORY_SENSITIVITY"
    TEMPORARY_PHYSICAL_DISCOMFORT = "TEMPORARY_PHYSICAL_DISCOMFORT"
    OTHER = "OTHER"


class ActivityToAvoid(str, enum.Enum):
    RUNNING = "RUNNING"
    JUMPING = "JUMPING"
    PROLONGED_STANDING = "PROLONGED_STANDING"
    ARMS_HANDS_USE = "ARMS_HANDS_USE"
    VISUAL_PRECISION = "VISUAL_PRECISION"
    LOUD_SOUNDS = "LOUD_SOUNDS"
    HIGH_STIMULATION = "HIGH_STIMULATION"
    PROLONGED_CONCENTRATION = "PROLONGED_CONCENTRATION"


class RepresentativeVerificationStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ConsentStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class ConsentPurposeType(str, enum.Enum):
    USAGE_MONITORING = "USAGE_MONITORING"


class AuditActorType(str, enum.Enum):
    ADULT = "ADULT"
    INTERNAL = "INTERNAL"


class AuditEventType(str, enum.Enum):
    REPRESENTATIVE_VERIFICATION_APPROVED = "REPRESENTATIVE_VERIFICATION_APPROVED"
    REPRESENTATIVE_VERIFICATION_REJECTED = "REPRESENTATIVE_VERIFICATION_REJECTED"
    CONSENT_GRANTED = "CONSENT_GRANTED"
    CONSENT_REVOKED = "CONSENT_REVOKED"
    DEVICE_DEACTIVATED = "DEVICE_DEACTIVATED"


class AuditResult(str, enum.Enum):
    SUCCESS = "SUCCESS"


class ParentalRespondent(str, enum.Enum):
    PARENT_OR_CAREGIVER = "parent_or_caregiver"


class ExperimentalPmumEstimateStatus(str, enum.Enum):
    ESTIMATED = "estimated"
    UNAVAILABLE = "unavailable"
    TECHNICAL_ERROR = "technical_error"


class HistoricalSubmissionStatus(str, enum.Enum):
    PENDING = "PENDING"
    SENDING = "SENDING"
    PROCESSED = "PROCESSED"
    CONFLICT = "CONFLICT"
    TECHNICAL_ERROR = "TECHNICAL_ERROR"


class UsageMetricStatus(str, enum.Enum):
    MEASURED = "MEASURED"
    UNAVAILABLE = "UNAVAILABLE"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    CAPTURE_ERROR = "CAPTURE_ERROR"
    INCOMPLETE = "INCOMPLETE"


class UsageCoverageStatus(str, enum.Enum):
    UNKNOWN = "UNKNOWN"
    PARTIAL = "PARTIAL"


class Profile(Base):
    __tablename__ = "profiles"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    alias: Mapped[str] = mapped_column(String(100))
    age: Mapped[int] = mapped_column(Integer)
    avatar: Mapped[Avatar] = mapped_column(Enum(Avatar, name="avatar_type"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    daily_usages: Mapped[list["DailyUsage"]] = relationship(back_populates="profile", cascade="all, delete-orphan")
    settings: Mapped["ProfileSettings | None"] = relationship(back_populates="profile", cascade="all, delete-orphan", uselist=False)
    contexts: Mapped[list["ProfileContext"]] = relationship(back_populates="profile", cascade="all, delete-orphan")
    activity_preferences: Mapped["ProfileActivityPreferences | None"] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
        uselist=False,
    )
    devices: Mapped[list["Device"]] = relationship(back_populates="profile", cascade="all, delete-orphan")
    link_codes: Mapped[list["ProfileLinkCode"]] = relationship(back_populates="profile", cascade="all, delete-orphan")
    bootstrap_tokens: Mapped[list["DeviceBootstrapToken"]] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
    )
    adult_links: Mapped[list["AdultProfileLink"]] = relationship(back_populates="profile", cascade="all, delete-orphan")
    parental_screen_time_responses: Mapped[list["ParentalScreenTimeResponse"]] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
    )


class ProfileSettings(Base):
    __tablename__ = "profile_settings"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), unique=True)
    school_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    school_end: Mapped[time | None] = mapped_column(Time, nullable=True)
    sleep_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    sleep_end: Mapped[time | None] = mapped_column(Time, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    profile: Mapped[Profile] = relationship(back_populates="settings")


class ProfileContext(Base):
    __tablename__ = "profile_contexts"
    __table_args__ = (Index("ix_profile_contexts_profile_start_date", "profile_id", "start_date"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    type: Mapped[ProfileContextType] = mapped_column(Enum(ProfileContextType, name="profile_context_type"))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    profile: Mapped[Profile] = relationship(back_populates="contexts")


class ProfileActivityPreferences(Base):
    __tablename__ = "profile_activity_preferences"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), unique=True)
    interests: Mapped[list[str]] = mapped_column(JSON, default=list)
    legacy_adaptation_type: Mapped[AdaptationType] = mapped_column(
        "adaptation_type",
        Enum(AdaptationType, name="profile_adaptation_type"),
        default=AdaptationType.NONE,
    )
    legacy_considerations: Mapped[list[str]] = mapped_column(
        "considerations",
        JSON,
        default=list,
    )
    considerations_answered: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
    )
    activities_to_avoid: Mapped[list[str]] = mapped_column(JSON, default=list)
    additional_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    legacy_valid_until: Mapped[date | None] = mapped_column(
        "valid_until",
        Date,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    profile: Mapped[Profile] = relationship(back_populates="activity_preferences")
    considerations: Mapped[list["ProfileActivityConsideration"]] = relationship(
        back_populates="preferences",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ProfileActivityConsideration.ordinal",
    )


class ProfileActivityConsideration(Base):
    __tablename__ = "profile_activity_considerations"
    __table_args__ = (
        CheckConstraint(
            "(duration_type = 'TEMPORARY' AND valid_until IS NOT NULL) OR "
            "(duration_type = 'PERMANENT' AND valid_until IS NULL)",
            name="ck_profile_activity_considerations_duration_valid_until",
        ),
        UniqueConstraint(
            "preferences_id",
            "type",
            name="uq_profile_activity_considerations_preferences_type",
        ),
        UniqueConstraint(
            "preferences_id",
            "ordinal",
            name="uq_profile_activity_considerations_preferences_ordinal",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    preferences_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profile_activity_preferences.id", ondelete="CASCADE"),
        index=True,
    )
    type: Mapped[ActivityConsiderationType] = mapped_column(
        Enum(ActivityConsiderationType, name="activity_consideration_type")
    )
    duration_type: Mapped[ConsiderationDurationType] = mapped_column(
        Enum(ConsiderationDurationType, name="consideration_duration_type")
    )
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    preferences: Mapped[ProfileActivityPreferences] = relationship(
        back_populates="considerations"
    )


class AdultAccount(Base):
    __tablename__ = "adult_accounts"
    __table_args__ = (UniqueConstraint("email", name="uq_adult_accounts_email"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    profile_links: Mapped[list["AdultProfileLink"]] = relationship(
        back_populates="adult",
        cascade="all, delete-orphan",
    )
    otp_challenges: Mapped[list["AdultOtpChallenge"]] = relationship(
        back_populates="adult",
        cascade="all, delete-orphan",
    )
    parental_screen_time_responses: Mapped[list["ParentalScreenTimeResponse"]] = relationship(
        back_populates="adult",
        cascade="all, delete-orphan",
    )


class AdultProfileLink(Base):
    __tablename__ = "adult_profile_links"
    __table_args__ = (
        UniqueConstraint("adult_id", "profile_id", name="uq_adult_profile_links_adult_profile"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    adult_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("adult_accounts.id", ondelete="CASCADE"), index=True)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    adult: Mapped[AdultAccount] = relationship(back_populates="profile_links")
    profile: Mapped[Profile] = relationship(back_populates="adult_links")


class RepresentativeVerification(Base):
    __tablename__ = "representative_verifications"
    __table_args__ = (
        UniqueConstraint(
            "adult_id",
            "profile_id",
            name="uq_representative_verifications_adult_profile",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    adult_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("adult_accounts.id", ondelete="CASCADE"), index=True)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    status: Mapped[RepresentativeVerificationStatus] = mapped_column(
        Enum(RepresentativeVerificationStatus, name="representative_verification_status"),
        default=RepresentativeVerificationStatus.PENDING,
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Consent(Base):
    __tablename__ = "consents"
    __table_args__ = (
        Index("ix_consents_status", "status"),
        Index("ix_consents_revoked_at", "revoked_at"),
        Index(
            "uq_consents_active_adult_profile",
            "adult_id",
            "profile_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
            sqlite_where=text("status = 'ACTIVE'"),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    adult_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("adult_accounts.id", ondelete="CASCADE"), index=True)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    status: Mapped[ConsentStatus] = mapped_column(Enum(ConsentStatus, name="consent_status"))
    privacy_notice_version: Mapped[str] = mapped_column(String(50))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    purposes: Mapped[list["ConsentPurpose"]] = relationship(
        back_populates="consent",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ConsentPurpose(Base):
    __tablename__ = "consent_purposes"
    consent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("consents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    purpose: Mapped[ConsentPurposeType] = mapped_column(
        Enum(ConsentPurposeType, name="consent_purpose_type"),
        primary_key=True,
    )
    consent: Mapped[Consent] = relationship(back_populates="purposes")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    actor_type: Mapped[AuditActorType] = mapped_column(Enum(AuditActorType, name="audit_actor_type"))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("profiles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[AuditEventType] = mapped_column(
        Enum(AuditEventType, name="audit_event_type"),
        index=True,
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    result: Mapped[AuditResult] = mapped_column(Enum(AuditResult, name="audit_result"))
    event_metadata: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)


class ParentalScreenTimeResponse(Base):
    __tablename__ = "parental_screen_time_responses"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "revision",
            name="uq_parental_screen_time_responses_profile_revision",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    adult_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("adult_accounts.id", ondelete="CASCADE"))
    revision: Mapped[int] = mapped_column(Integer)
    age: Mapped[int] = mapped_column(Integer)
    sex_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weekday_category: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weekend_category: Mapped[int | None] = mapped_column(Integer, nullable=True)
    respondent: Mapped[ParentalRespondent] = mapped_column(
        Enum(ParentalRespondent, name="parental_respondent"),
    )
    reference_days: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    profile: Mapped[Profile] = relationship(back_populates="parental_screen_time_responses")
    adult: Mapped[AdultAccount] = relationship(back_populates="parental_screen_time_responses")
    estimates: Mapped[list["ExperimentalPmumEstimate"]] = relationship(
        back_populates="parental_response",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ExperimentalPmumEstimate(Base):
    __tablename__ = "experimental_pmum_estimates"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    parental_response_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("parental_screen_time_responses.id", ondelete="CASCADE"),
        index=True,
    )
    status: Mapped[ExperimentalPmumEstimateStatus] = mapped_column(
        Enum(ExperimentalPmumEstimateStatus, name="experimental_pmum_estimate_status"),
    )
    estimated_pmum_sf_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    estimated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    technical_error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    technical_error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    parental_response: Mapped[ParentalScreenTimeResponse] = relationship(
        back_populates="estimates",
    )


class AdultOtpChallenge(Base):
    __tablename__ = "adult_otp_challenges"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    adult_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("adult_accounts.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    adult: Mapped[AdultAccount] = relationship(back_populates="otp_challenges")


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("installation_id", name="uq_devices_installation_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    installation_id: Mapped[str] = mapped_column(String(255))
    platform: Mapped[str] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    profile: Mapped[Profile] = relationship(back_populates="devices")


class ActivityOffer(Base):
    __tablename__ = "activity_offers"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"),
        index=True,
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"),
        index=True,
    )
    context: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        index=True,
    )
    items: Mapped[list["ActivityOfferItem"]] = relationship(
        back_populates="offer",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ActivityOfferItem.ordinal",
    )
    selections: Mapped[list["ActivitySelection"]] = relationship(
        back_populates="offer",
        cascade="all, delete-orphan",
    )


class ActivityOfferItem(Base):
    __tablename__ = "activity_offer_items"
    __table_args__ = (
        UniqueConstraint(
            "offer_id",
            "activity_id",
            name="uq_activity_offer_items_offer_activity",
        ),
    )
    offer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("activity_offers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    activity_id: Mapped[str] = mapped_column(String(100))
    offer: Mapped[ActivityOffer] = relationship(back_populates="items")


class ActivitySelection(Base):
    __tablename__ = "activity_selections"
    selection_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    offer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("activity_offers.id", ondelete="CASCADE"),
        index=True,
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"),
        index=True,
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"),
        index=True,
    )
    activity_id: Mapped[str] = mapped_column(String(100))
    selected_at_epoch_millis: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        index=True,
    )
    offer: Mapped[ActivityOffer] = relationship(back_populates="selections")


class HistoricalSubmission(Base):
    __tablename__ = "historical_submissions"
    __table_args__ = (
        CheckConstraint(
            "assignment_id = device_id",
            name="ck_historical_submissions_assignment_is_device",
        ),
        CheckConstraint(
            "attempts >= 0",
            name="ck_historical_submissions_attempts_non_negative",
        ),
        CheckConstraint(
            "interval_start < interval_end",
            name="ck_historical_submissions_interval",
        ),
        UniqueConstraint(
            "series_key",
            "local_date",
            name="uq_historical_submissions_series_local_date",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    request_id: Mapped[str] = mapped_column(String(128), unique=True)
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"),
        index=True,
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"),
        index=True,
    )
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"),
        index=True,
    )
    local_date: Mapped[date] = mapped_column(Date)
    time_zone: Mapped[str] = mapped_column(String(128))
    measurement_version: Mapped[str] = mapped_column(String(100))
    bridge_version: Mapped[str] = mapped_column(String(100))
    tzdata_version: Mapped[str] = mapped_column(String(50))
    series_key: Mapped[str] = mapped_column(String(71), index=True)
    representation_record_id: Mapped[str] = mapped_column(String(68), unique=True)
    interval_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    interval_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    payload_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[HistoricalSubmissionStatus] = mapped_column(
        Enum(HistoricalSubmissionStatus, name="historical_submission_status"),
        default=HistoricalSubmissionStatus.PENDING,
        index=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    analytics_response: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=True,
    )
    analytics_evaluation_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    analytics_replayed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
    )


class ProfileLinkCode(Base):
    __tablename__ = "profile_link_codes"
    __table_args__ = (UniqueConstraint("code_hash", name="uq_profile_link_codes_code_hash"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(32))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    profile: Mapped[Profile] = relationship(back_populates="link_codes")


class DeviceBootstrapToken(Base):
    __tablename__ = "device_bootstrap_tokens"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_device_bootstrap_tokens_hash"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    profile: Mapped[Profile] = relationship(back_populates="bootstrap_tokens")


class DailyUsage(Base):
    __tablename__ = "daily_usages"
    __table_args__ = (
        UniqueConstraint("profile_id", "date", name="uq_daily_usages_profile_date"),
        Index("ix_daily_usages_profile_date", "profile_id", "date"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    total_usage_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    session_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    night_usage_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_night_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    max_session_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unlock_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_usage_status: Mapped[UsageMetricStatus] = mapped_column(Enum(UsageMetricStatus, name="usage_metric_status"), default=UsageMetricStatus.UNAVAILABLE)
    session_count_status: Mapped[UsageMetricStatus] = mapped_column(Enum(UsageMetricStatus, name="usage_metric_status"), default=UsageMetricStatus.UNAVAILABLE)
    night_usage_status: Mapped[UsageMetricStatus] = mapped_column(Enum(UsageMetricStatus, name="usage_metric_status"), default=UsageMetricStatus.UNAVAILABLE)
    last_night_activity_status: Mapped[UsageMetricStatus] = mapped_column(Enum(UsageMetricStatus, name="usage_metric_status"), default=UsageMetricStatus.UNAVAILABLE)
    max_session_status: Mapped[UsageMetricStatus] = mapped_column(Enum(UsageMetricStatus, name="usage_metric_status"), default=UsageMetricStatus.UNAVAILABLE)
    unlock_count_status: Mapped[UsageMetricStatus] = mapped_column(Enum(UsageMetricStatus, name="usage_metric_status"), default=UsageMetricStatus.UNAVAILABLE)
    applications_status: Mapped[UsageMetricStatus] = mapped_column(Enum(UsageMetricStatus, name="usage_metric_status"), default=UsageMetricStatus.UNAVAILABLE)
    applications_present: Mapped[bool] = mapped_column(Boolean, default=False)
    coverage_status: Mapped[UsageCoverageStatus] = mapped_column(Enum(UsageCoverageStatus, name="usage_coverage_status"), default=UsageCoverageStatus.UNKNOWN)
    period_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timezone_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_unit: Mapped[str] = mapped_column(String(16), default="MINUTES")
    measurement_method: Mapped[str | None] = mapped_column(String(80), nullable=True)
    calculation_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    night_window_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    night_window_end: Mapped[time | None] = mapped_column(Time, nullable=True)
    periods_uniform: Mapped[bool] = mapped_column(Boolean, default=False)
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    profile: Mapped[Profile] = relationship(back_populates="daily_usages")
    applications: Mapped[list["AppUsage"]] = relationship(back_populates="daily_usage", cascade="all, delete-orphan", lazy="selectin")


class AppUsage(Base):
    __tablename__ = "app_usages"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    daily_usage_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("daily_usages.id", ondelete="CASCADE"), index=True)
    package_name: Mapped[str] = mapped_column(String(255))
    app_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    usage_minutes: Mapped[int] = mapped_column(Integer)
    daily_usage: Mapped[DailyUsage] = relationship(back_populates="applications")


class UsageSnapshot(Base):
    __tablename__ = "usage_snapshots"
    __table_args__ = (
        Index("ix_usage_snapshots_profile_date", "profile_id", "date"),
        Index("ix_usage_snapshots_device_date_captured", "device_id", "date", "captured_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    timezone_name: Mapped[str] = mapped_column(String(64))
    duration_unit: Mapped[str] = mapped_column(String(16))
    measurement_method: Mapped[str] = mapped_column(String(80))
    calculation_version: Mapped[str] = mapped_column(String(80))
    coverage_status: Mapped[UsageCoverageStatus] = mapped_column(Enum(UsageCoverageStatus, name="usage_coverage_status"))
    total_usage_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    session_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    night_usage_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_night_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    max_session_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unlock_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_usage_status: Mapped[UsageMetricStatus] = mapped_column(Enum(UsageMetricStatus, name="usage_metric_status"))
    session_count_status: Mapped[UsageMetricStatus] = mapped_column(Enum(UsageMetricStatus, name="usage_metric_status"))
    night_usage_status: Mapped[UsageMetricStatus] = mapped_column(Enum(UsageMetricStatus, name="usage_metric_status"))
    last_night_activity_status: Mapped[UsageMetricStatus] = mapped_column(Enum(UsageMetricStatus, name="usage_metric_status"))
    max_session_status: Mapped[UsageMetricStatus] = mapped_column(Enum(UsageMetricStatus, name="usage_metric_status"))
    unlock_count_status: Mapped[UsageMetricStatus] = mapped_column(Enum(UsageMetricStatus, name="usage_metric_status"))
    applications_status: Mapped[UsageMetricStatus] = mapped_column(Enum(UsageMetricStatus, name="usage_metric_status"))
    applications_present: Mapped[bool] = mapped_column(Boolean)
    night_window_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    night_window_end: Mapped[time | None] = mapped_column(Time, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    applications: Mapped[list["SnapshotAppUsage"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class SnapshotAppUsage(Base):
    __tablename__ = "snapshot_app_usages"
    __table_args__ = (UniqueConstraint("snapshot_id", "package_name", name="uq_snapshot_app_usages_snapshot_package"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("usage_snapshots.id", ondelete="CASCADE"), index=True)
    package_name: Mapped[str] = mapped_column(String(255))
    app_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    usage_minutes: Mapped[int] = mapped_column(Integer)


class DeviceDailyUsageHead(Base):
    __tablename__ = "device_daily_usage_heads"
    __table_args__ = (UniqueConstraint("device_id", "date", name="uq_device_daily_usage_heads_device_date"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("usage_snapshots.id", ondelete="CASCADE"), unique=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AnalysisResult(Base):
    __tablename__ = "analysis_results"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    daily_usage_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("daily_usages.id", ondelete="CASCADE"), index=True)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    anomaly_detected: Mapped[bool] = mapped_column(Boolean)
    anomaly_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_level: Mapped[RiskLevel] = mapped_column(Enum(RiskLevel, name="risk_level"))
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(80), nullable=True)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    analysis_result_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_results.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(150))
    message: Mapped[str] = mapped_column(Text)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
