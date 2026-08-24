"""Persisted conversation state, booking drafts and confirmation nonces."""

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
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidPkMixin, VersionMixin, encrypted_json
from app.db.types import Ciphertext, JsonDocument, UuidBinary
from app.domain.enums import (
    BookingDraftStatus,
    ConfirmationAction,
    DateMode,
    LocationMode,
    SearchType,
    SortStrategy,
)


class ConversationState(TimestampMixin, VersionMixin, Base):
    """Restart-safe finite state machine position for one Telegram user."""

    __tablename__ = "conversation_states"

    telegram_user_id: Mapped[uuid.UUID] = mapped_column(
        UuidBinary, ForeignKey("telegram_users.id", ondelete="CASCADE"), primary_key=True
    )
    state: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_ciphertext: Mapped[bytes | None] = mapped_column(Ciphertext(16384), default=None)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)

    payload = encrypted_json("payload_ciphertext")

    @property
    def id(self) -> uuid.UUID:
        """Alias used by the encrypted-attribute descriptor."""
        return self.telegram_user_id


class BookingDraft(UuidPkMixin, TimestampMixin, VersionMixin, Base):
    """In-progress manual booking or watch configuration."""

    __tablename__ = "booking_drafts"
    __table_args__ = (Index("ix_booking_drafts_user_status", "telegram_user_id", "status"),)

    telegram_user_id: Mapped[uuid.UUID] = mapped_column(
        UuidBinary, ForeignKey("telegram_users.id", ondelete="CASCADE"), nullable=False
    )
    portal_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidBinary, ForeignKey("portal_accounts.id", ondelete="CASCADE"), default=None
    )
    patient_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidBinary, ForeignKey("patients.id", ondelete="CASCADE"), default=None
    )
    search_type: Mapped[SearchType | None] = mapped_column(
        SAEnum(SearchType, native_enum=False, length=16), default=None
    )
    query_text: Mapped[str | None] = mapped_column(String(160), default=None)
    selected_service_external_id: Mapped[str | None] = mapped_column(String(64), default=None)
    selected_service_name: Mapped[str | None] = mapped_column(String(255), default=None)
    selected_specification_id: Mapped[str | None] = mapped_column(String(64), default=None)
    selected_doctor_external_id: Mapped[str | None] = mapped_column(String(128), default=None)
    selected_doctor_name: Mapped[str | None] = mapped_column(String(255), default=None)
    service_online_bookable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    preparation_note: Mapped[str | None] = mapped_column(String(1024), default=None)
    location_mode: Mapped[LocationMode] = mapped_column(
        SAEnum(LocationMode, native_enum=False, length=16),
        default=LocationMode.ALL_DYNAMIC,
        nullable=False,
    )
    selected_locations_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JsonDocument, default=list, nullable=False
    )
    available_locations_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JsonDocument, default=list, nullable=False
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
    selected_slot_ciphertext: Mapped[bytes | None] = mapped_column(Ciphertext(8192), default=None)
    slot_cache_ciphertext: Mapped[bytes | None] = mapped_column(
        Ciphertext(16_777_215), default=None
    )
    slot_cache_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    status: Mapped[BookingDraftStatus] = mapped_column(
        SAEnum(BookingDraftStatus, native_enum=False, length=16),
        default=BookingDraftStatus.OPEN,
        nullable=False,
    )
    origin_watch_id: Mapped[uuid.UUID | None] = mapped_column(UuidBinary, default=None)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)

    selected_slot = encrypted_json("selected_slot_ciphertext")
    slot_cache = encrypted_json("slot_cache_ciphertext")


class ConfirmationNonce(UuidPkMixin, TimestampMixin, Base):
    """Single-use, expiring token backing every destructive confirmation."""

    __tablename__ = "confirmation_nonces"
    __table_args__ = (
        Index("ix_confirmation_nonces_user_action", "telegram_user_id", "action_type"),
    )

    telegram_user_id: Mapped[uuid.UUID] = mapped_column(
        UuidBinary, ForeignKey("telegram_users.id", ondelete="CASCADE"), nullable=False
    )
    action_type: Mapped[ConfirmationAction] = mapped_column(
        SAEnum(ConfirmationAction, native_enum=False, length=32), nullable=False
    )
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    nonce_hash: Mapped[bytes] = mapped_column(BINARY(32), unique=True, nullable=False)
    context_hash: Mapped[bytes] = mapped_column(BINARY(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255), default=None)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    def is_usable(self, now: datetime) -> bool:
        return self.consumed_at is None and self.expires_at > now


class CallbackConsumption(UuidPkMixin, TimestampMixin, Base):
    """Idempotency ledger for Telegram callbacks and updates."""

    __tablename__ = "callback_consumptions"

    dedup_key: Mapped[bytes] = mapped_column(BINARY(32), unique=True, nullable=False)
    telegram_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidBinary, ForeignKey("telegram_users.id", ondelete="CASCADE"), default=None
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
