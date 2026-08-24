"""Availability monitors, their locations and the slots already seen."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BINARY,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Time,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import (
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UuidPkMixin,
    VersionMixin,
)
from app.db.types import JsonDocument, UuidBinary
from app.domain.enums import (
    DateMode,
    LocationMode,
    SearchType,
    SortStrategy,
    WatchCompletionPolicy,
    WatchMode,
    WatchStatus,
)


class BookingWatch(UuidPkMixin, TimestampMixin, VersionMixin, SoftDeleteMixin, Base):
    """A saved availability monitor."""

    __tablename__ = "booking_watches"
    __table_args__ = (
        Index("ix_booking_watches_due", "status", "next_check_at"),
        Index("ix_booking_watches_account_status", "portal_account_id", "status"),
        Index("ix_booking_watches_user_status", "telegram_user_id", "status"),
    )

    telegram_user_id: Mapped[uuid.UUID] = mapped_column(
        UuidBinary, ForeignKey("telegram_users.id", ondelete="CASCADE"), nullable=False
    )
    portal_account_id: Mapped[uuid.UUID] = mapped_column(
        UuidBinary, ForeignKey("portal_accounts.id", ondelete="CASCADE"), nullable=False
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UuidBinary, ForeignKey("patients.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    search_type: Mapped[SearchType] = mapped_column(
        SAEnum(SearchType, native_enum=False, length=16), nullable=False
    )
    query_text: Mapped[str] = mapped_column(String(160), nullable=False)
    selected_service_external_id: Mapped[str | None] = mapped_column(String(64), default=None)
    selected_service_name: Mapped[str | None] = mapped_column(String(255), default=None)
    selected_specification_id: Mapped[str | None] = mapped_column(String(64), default=None)
    selected_doctor_external_id: Mapped[str | None] = mapped_column(String(128), default=None)
    selected_doctor_name: Mapped[str | None] = mapped_column(String(255), default=None)
    location_mode: Mapped[LocationMode] = mapped_column(
        SAEnum(LocationMode, native_enum=False, length=16),
        default=LocationMode.ALL_DYNAMIC,
        nullable=False,
    )
    date_mode: Mapped[DateMode] = mapped_column(
        SAEnum(DateMode, native_enum=False, length=16), default=DateMode.ROLLING, nullable=False
    )
    date_from: Mapped[date | None] = mapped_column(Date, default=None)
    date_to: Mapped[date | None] = mapped_column(Date, default=None)
    rolling_days: Mapped[int | None] = mapped_column(Integer, default=30)
    min_lead_hours: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    weekdays_mask: Mapped[int] = mapped_column(Integer, default=0b1111111, nullable=False)
    time_windows_json: Mapped[list[dict[str, str]]] = mapped_column(
        JsonDocument, default=list, nullable=False
    )
    excluded_dates_json: Mapped[list[str]] = mapped_column(
        JsonDocument, default=list, nullable=False
    )
    sort_strategy: Mapped[SortStrategy] = mapped_column(
        SAEnum(SortStrategy, native_enum=False, length=32),
        default=SortStrategy.EARLIEST,
        nullable=False,
    )
    max_results: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    mode: Mapped[WatchMode] = mapped_column(
        SAEnum(WatchMode, native_enum=False, length=24), default=WatchMode.NOTIFY, nullable=False
    )
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_only_changes: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    quiet_hours_start: Mapped[Any | None] = mapped_column(Time, default=None)
    quiet_hours_end: Mapped[Any | None] = mapped_column(Time, default=None)
    completion_policy: Mapped[WatchCompletionPolicy] = mapped_column(
        SAEnum(WatchCompletionPolicy, native_enum=False, length=32),
        default=WatchCompletionPolicy.COMPLETE_ON_SUCCESS,
        nullable=False,
    )
    status: Mapped[WatchStatus] = mapped_column(
        SAEnum(WatchStatus, native_enum=False, length=16), default=WatchStatus.DRAFT, nullable=False
    )
    interval_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    last_run_now_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    last_slot_fingerprint: Mapped[str | None] = mapped_column(String(64), default=None)
    consecutive_errors: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String(64), default=None)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    missing_location_reported_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    lease_owner: Mapped[str | None] = mapped_column(String(64), default=None)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    completed_reason: Mapped[str | None] = mapped_column(String(64), default=None)

    locations: Mapped[list[WatchLocation]] = relationship(
        back_populates="watch", lazy="selectin", cascade="all, delete-orphan"
    )

    @property
    def is_active(self) -> bool:
        return self.status is WatchStatus.ACTIVE and self.deleted_at is None


class WatchLocation(UuidPkMixin, TimestampMixin, Base):
    """A portal location explicitly selected for a monitor."""

    __tablename__ = "watch_locations"
    __table_args__ = (UniqueConstraint("watch_id", "portal_location_id"),)

    watch_id: Mapped[uuid.UUID] = mapped_column(
        UuidBinary, ForeignKey("booking_watches.id", ondelete="CASCADE"), nullable=False
    )
    portal_location_id: Mapped[str] = mapped_column(String(64), nullable=False)
    location_name: Mapped[str] = mapped_column(String(255), nullable=False)
    preference_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    watch: Mapped[BookingWatch] = relationship(back_populates="locations", lazy="noload")


class SeenSlot(UuidPkMixin, Base):
    """Slots already observed by a monitor, used to notify only real changes."""

    __tablename__ = "seen_slots"
    __table_args__ = (
        UniqueConstraint("watch_id", "slot_fingerprint"),
        Index("ix_seen_slots_watch_available", "watch_id", "currently_available"),
    )

    watch_id: Mapped[uuid.UUID] = mapped_column(
        UuidBinary, ForeignKey("booking_watches.id", ondelete="CASCADE"), nullable=False
    )
    slot_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    currently_available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: Non-sensitive normalised metadata: location id/name and slot timestamp.
    slot_starts_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    location_id: Mapped[str | None] = mapped_column(String(64), default=None)
    location_name: Mapped[str | None] = mapped_column(String(255), default=None)


class WatchRunLog(UuidPkMixin, Base):
    """Compact, non-sensitive execution history shown to administrators."""

    __tablename__ = "watch_run_logs"
    __table_args__ = (Index("ix_watch_run_logs_watch_started", "watch_id", "started_at"),)

    watch_id: Mapped[uuid.UUID] = mapped_column(
        UuidBinary, ForeignKey("booking_watches.id", ondelete="CASCADE"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    slots_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    new_slots: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), default=None)
    correlation_id: Mapped[str | None] = mapped_column(String(32), default=None)
    worker_id: Mapped[str | None] = mapped_column(String(64), default=None)
    duration_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    lease_token: Mapped[bytes | None] = mapped_column(BINARY(16), default=None)
