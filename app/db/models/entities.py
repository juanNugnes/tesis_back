import enum
import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import Boolean, Date, DateTime, Enum, Float, ForeignKey, Index, Integer, JSON, String, Text, Time, UniqueConstraint, true
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
