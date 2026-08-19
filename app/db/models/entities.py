import enum
import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import Boolean, Date, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text, Time, UniqueConstraint
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
