import enum
import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import Boolean, Date, DateTime, Enum, Float, ForeignKey, Index, Integer, JSON, String, Text, Time, UniqueConstraint, text, true
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


class AuditResult(str, enum.Enum):
    SUCCESS = "SUCCESS"


class ParentalRespondent(str, enum.Enum):
    PARENT_OR_CAREGIVER = "parent_or_caregiver"


class ExperimentalPmumEstimateStatus(str, enum.Enum):
    ESTIMATED = "estimated"
    UNAVAILABLE = "unavailable"
    TECHNICAL_ERROR = "technical_error"


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
    adaptation_type: Mapped[AdaptationType] = mapped_column(Enum(AdaptationType, name="profile_adaptation_type"))
    considerations: Mapped[list[str]] = mapped_column(JSON, default=list)
    activities_to_avoid: Mapped[list[str]] = mapped_column(JSON, default=list)
    additional_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    profile: Mapped[Profile] = relationship(back_populates="activity_preferences")


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
    total_usage_minutes: Mapped[int] = mapped_column(Integer)
    session_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    night_usage_minutes: Mapped[int] = mapped_column(Integer)
    max_session_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unlock_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
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
