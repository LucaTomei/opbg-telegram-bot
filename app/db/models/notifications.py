"""Outbound notification queue."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BINARY,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidPkMixin, encrypted_json
from app.db.types import Ciphertext, UuidBinary
from app.domain.enums import NotificationEventType, NotificationStatus


class NotificationEvent(UuidPkMixin, TimestampMixin, Base):
    """A queued Telegram notification with deduplication and retry state."""

    __tablename__ = "notification_events"
    __table_args__ = (
        Index("ix_notification_events_dedup", "dedup_key", unique=True),
        Index("ix_notification_events_pending", "status", "next_attempt_at"),
        Index("ix_notification_events_user_created", "telegram_user_id", "created_at"),
    )

    telegram_user_id: Mapped[uuid.UUID] = mapped_column(
        UuidBinary, ForeignKey("telegram_users.id", ondelete="CASCADE"), nullable=False
    )
    watch_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidBinary, ForeignKey("booking_watches.id", ondelete="CASCADE"), default=None
    )
    appointment_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidBinary, ForeignKey("appointments_cache.id", ondelete="CASCADE"), default=None
    )
    portal_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidBinary, ForeignKey("portal_accounts.id", ondelete="CASCADE"), default=None
    )
    booking_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidBinary, ForeignKey("booking_attempts.id", ondelete="SET NULL"), default=None
    )
    event_type: Mapped[NotificationEventType] = mapped_column(
        SAEnum(NotificationEventType, native_enum=False, length=32), nullable=False
    )
    dedup_key: Mapped[bytes] = mapped_column(BINARY(32), nullable=False)
    status: Mapped[NotificationStatus] = mapped_column(
        SAEnum(NotificationStatus, native_enum=False, length=16),
        default=NotificationStatus.PENDING,
        nullable=False,
    )
    payload_ciphertext: Mapped[bytes | None] = mapped_column(Ciphertext(16384), default=None)
    telegram_message_id: Mapped[int | None] = mapped_column(Integer, default=None)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    suppressed_reason: Mapped[str | None] = mapped_column(String(64), default=None)
    last_error_code: Mapped[str | None] = mapped_column(String(64), default=None)
    correlation_id: Mapped[str | None] = mapped_column(String(32), default=None)

    payload: Any = encrypted_json("payload_ciphertext")
