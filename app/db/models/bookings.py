"""Booking attempts and the cached appointment list."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BINARY,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UuidPkMixin,
    VersionMixin,
    encrypted_text,
)
from app.db.types import Ciphertext, UuidBinary
from app.domain.enums import AppointmentStatus, BookingAttemptStatus, BookingStage


class BookingAttempt(UuidPkMixin, TimestampMixin, VersionMixin, Base):
    """One idempotent attempt to book a specific slot."""

    __tablename__ = "booking_attempts"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_booking_attempts_idempotency_key"),
        UniqueConstraint("watch_slot_success_key", name="uq_booking_attempts_watch_slot_success"),
        Index("ix_booking_attempts_account_status", "portal_account_id", "status"),
        Index("ix_booking_attempts_user_started", "telegram_user_id", "started_at"),
    )

    telegram_user_id: Mapped[uuid.UUID] = mapped_column(
        UuidBinary, ForeignKey("telegram_users.id", ondelete="CASCADE"), nullable=False
    )
    portal_account_id: Mapped[uuid.UUID] = mapped_column(
        UuidBinary, ForeignKey("portal_accounts.id", ondelete="CASCADE"), nullable=False
    )
    patient_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidBinary, ForeignKey("patients.id", ondelete="SET NULL"), default=None
    )
    watch_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidBinary, ForeignKey("booking_watches.id", ondelete="SET NULL"), default=None
    )
    draft_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidBinary, ForeignKey("booking_drafts.id", ondelete="SET NULL"), default=None
    )
    initiated_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidBinary, ForeignKey("telegram_users.id", ondelete="SET NULL"), default=None
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    slot_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: ``NULL`` unless the attempt succeeded for a watch; the unique constraint
    #: then guarantees a watch books a given slot at most once.
    watch_slot_success_key: Mapped[str | None] = mapped_column(String(96), default=None)
    status: Mapped[BookingAttemptStatus] = mapped_column(
        SAEnum(BookingAttemptStatus, native_enum=False, length=16),
        default=BookingAttemptStatus.STARTED,
        nullable=False,
    )
    stage: Mapped[BookingStage] = mapped_column(
        SAEnum(BookingStage, native_enum=False, length=32),
        default=BookingStage.CREATED,
        nullable=False,
    )
    portal_appointment_external_id: Mapped[str | None] = mapped_column(String(64), default=None)
    error_code: Mapped[str | None] = mapped_column(String(64), default=None)
    error_detail: Mapped[str | None] = mapped_column(String(512), default=None)
    correlation_id: Mapped[str | None] = mapped_column(String(32), default=None)
    slot_starts_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    location_name: Mapped[str | None] = mapped_column(String(255), default=None)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    confirm_sent_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    snapshot_before_hash: Mapped[bytes | None] = mapped_column(BINARY(32), default=None)
    snapshot_after_hash: Mapped[bytes | None] = mapped_column(BINARY(32), default=None)


class AppointmentCache(UuidPkMixin, TimestampMixin, VersionMixin, SoftDeleteMixin, Base):
    """Local mirror of an appointment shown in the portal appointment list."""

    __tablename__ = "appointments_cache"
    __table_args__ = (
        UniqueConstraint("portal_account_id", "portal_external_id"),
        Index("ix_appointments_cache_user_scheduled", "telegram_user_id", "scheduled_at_utc"),
        Index("ix_appointments_cache_status", "status"),
    )

    telegram_user_id: Mapped[uuid.UUID] = mapped_column(
        UuidBinary, ForeignKey("telegram_users.id", ondelete="CASCADE"), nullable=False
    )
    portal_account_id: Mapped[uuid.UUID] = mapped_column(
        UuidBinary, ForeignKey("portal_accounts.id", ondelete="CASCADE"), nullable=False
    )
    patient_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidBinary, ForeignKey("patients.id", ondelete="SET NULL"), default=None
    )
    portal_external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    service_name_ciphertext: Mapped[bytes | None] = mapped_column(Ciphertext(1024), default=None)
    doctor_name_ciphertext: Mapped[bytes | None] = mapped_column(Ciphertext(1024), default=None)
    location_name_ciphertext: Mapped[bytes | None] = mapped_column(Ciphertext(1024), default=None)
    patient_label_ciphertext: Mapped[bytes | None] = mapped_column(Ciphertext(1024), default=None)
    scheduled_at_utc: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    status: Mapped[AppointmentStatus] = mapped_column(
        SAEnum(AppointmentStatus, native_enum=False, length=16),
        default=AppointmentStatus.PENDING,
        nullable=False,
    )
    payment_note: Mapped[str | None] = mapped_column(String(255), default=None)
    pdf_available: Mapped[bool] = mapped_column(default=True, nullable=False)
    cancel_path: Mapped[str | None] = mapped_column(String(255), default=None)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    raw_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    cancellation_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    service_name = encrypted_text("service_name_ciphertext")
    doctor_name = encrypted_text("doctor_name_ciphertext")
    location_name = encrypted_text("location_name_ciphertext")
    patient_label = encrypted_text("patient_label_ciphertext")


class CancellationAttempt(UuidPkMixin, TimestampMixin, Base):
    """One idempotent attempt to cancel an appointment."""

    __tablename__ = "cancellation_attempts"
    __table_args__ = (UniqueConstraint("idempotency_key"),)

    telegram_user_id: Mapped[uuid.UUID] = mapped_column(
        UuidBinary, ForeignKey("telegram_users.id", ondelete="CASCADE"), nullable=False
    )
    portal_account_id: Mapped[uuid.UUID] = mapped_column(
        UuidBinary, ForeignKey("portal_accounts.id", ondelete="CASCADE"), nullable=False
    )
    appointment_id: Mapped[uuid.UUID] = mapped_column(
        UuidBinary, ForeignKey("appointments_cache.id", ondelete="CASCADE"), nullable=False
    )
    initiated_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidBinary, ForeignKey("telegram_users.id", ondelete="SET NULL"), default=None
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="STARTED")
    error_code: Mapped[str | None] = mapped_column(String(64), default=None)
    error_detail: Mapped[str | None] = mapped_column(String(512), default=None)
    correlation_id: Mapped[str | None] = mapped_column(String(32), default=None)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
