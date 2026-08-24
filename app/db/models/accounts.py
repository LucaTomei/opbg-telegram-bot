"""Portal accounts, patients and one-time connection tickets."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BINARY,
    Boolean,
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import (
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UuidPkMixin,
    VersionMixin,
    encrypted_json,
    encrypted_text,
)
from app.db.types import BlindIndexColumn, Ciphertext, UuidBinary
from app.domain.enums import PortalAccountStatus

if TYPE_CHECKING:
    from app.db.models.users import TelegramUser


class PortalAccount(UuidPkMixin, TimestampMixin, VersionMixin, SoftDeleteMixin, Base):
    """A portal login owned by exactly one Telegram user."""

    __tablename__ = "portal_accounts"
    __table_args__ = (
        UniqueConstraint(
            "telegram_user_id",
            "email_blind_index",
            "deleted_marker",
            name="uq_portal_accounts_user_email_alive",
        ),
        Index("ix_portal_accounts_status", "status"),
    )

    telegram_user_id: Mapped[uuid.UUID] = mapped_column(
        UuidBinary, ForeignKey("telegram_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    email_ciphertext: Mapped[bytes | None] = mapped_column(Ciphertext(1024), default=None)
    email_blind_index: Mapped[bytes] = mapped_column(BlindIndexColumn, nullable=False, index=True)
    password_ciphertext: Mapped[bytes | None] = mapped_column(Ciphertext(2048), default=None)
    cookie_jar_ciphertext: Mapped[bytes | None] = mapped_column(Ciphertext(16384), default=None)
    portal_metadata_ciphertext: Mapped[bytes | None] = mapped_column(Ciphertext(8192), default=None)
    status: Mapped[PortalAccountStatus] = mapped_column(
        SAEnum(PortalAccountStatus, native_enum=False, length=24),
        default=PortalAccountStatus.ACTIVE,
        nullable=False,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    last_error_code: Mapped[str | None] = mapped_column(String(64), default=None)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    consecutive_auth_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    encryption_key_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    #: Constant ``0`` while alive, set to the row id when soft deleted so the
    #: uniqueness constraint only applies to live accounts.
    deleted_marker: Mapped[bytes] = mapped_column(BINARY(16), default=b"\x00" * 16, nullable=False)

    email = encrypted_text("email_ciphertext")
    password = encrypted_text("password_ciphertext")
    cookie_jar = encrypted_json("cookie_jar_ciphertext")
    portal_metadata = encrypted_json("portal_metadata_ciphertext")

    user: Mapped[TelegramUser] = relationship(back_populates="portal_accounts", lazy="noload")
    patients: Mapped[list[Patient]] = relationship(back_populates="account", lazy="noload")

    @property
    def is_usable(self) -> bool:
        return self.status is PortalAccountStatus.ACTIVE and self.deleted_at is None

    def mark_deleted(self) -> None:
        """Soft delete the account and release the unique alive constraint."""
        self.deleted_marker = self.id.bytes
        self.status = PortalAccountStatus.DELETED


class Patient(UuidPkMixin, TimestampMixin, VersionMixin, SoftDeleteMixin, Base):
    """A patient bookable through a portal account."""

    __tablename__ = "patients"
    __table_args__ = (
        UniqueConstraint(
            "portal_account_id",
            "external_tax_code_blind_index",
            name="uq_patients_account_tax_code",
        ),
    )

    portal_account_id: Mapped[uuid.UUID] = mapped_column(
        UuidBinary, ForeignKey("portal_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_tax_code_ciphertext: Mapped[bytes] = mapped_column(Ciphertext(512), nullable=False)
    external_tax_code_blind_index: Mapped[bytes] = mapped_column(BlindIndexColumn, nullable=False)
    display_name_ciphertext: Mapped[bytes | None] = mapped_column(Ciphertext(1024), default=None)
    relationship_ciphertext: Mapped[bytes | None] = mapped_column(Ciphertext(512), default=None)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    external_tax_code = encrypted_text("external_tax_code_ciphertext")
    display_name = encrypted_text("display_name_ciphertext")
    relation = encrypted_text("relationship_ciphertext")

    account: Mapped[PortalAccount] = relationship(back_populates="patients", lazy="noload")


class ConnectRateLimit(UuidPkMixin, TimestampMixin, Base):
    """Sliding counter protecting the public connect form."""

    __tablename__ = "connect_rate_limits"
    __table_args__ = (UniqueConstraint("scope", "subject_hash", "window_start"),)

    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_hash: Mapped[bytes] = mapped_column(BINARY(32), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    counter: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
