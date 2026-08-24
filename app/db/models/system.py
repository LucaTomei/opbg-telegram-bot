"""Administrative audit trail, system events, worker heartbeats and breakers."""

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
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidPkMixin
from app.db.types import JsonDocument, UuidBinary
from app.domain.enums import AuditAction, BreakerScope, BreakerState, Severity


class AdminAuditLog(UuidPkMixin, Base):
    """Append-only administrative audit trail.

    Updates and deletes are rejected by :mod:`app.db.repositories.audit`; the
    absence of ``updated_at`` and of any mutable column is intentional.
    """

    __tablename__ = "admin_audit_log"
    __table_args__ = (
        Index("ix_admin_audit_log_admin_created", "admin_user_id", "created_at"),
        Index("ix_admin_audit_log_target", "target_type", "target_id"),
        Index("ix_admin_audit_log_correlation", "correlation_id"),
    )

    admin_user_id: Mapped[uuid.UUID] = mapped_column(
        UuidBinary, ForeignKey("telegram_users.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[AuditAction] = mapped_column(
        SAEnum(AuditAction, native_enum=False, length=40), nullable=False
    )
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(512), default=None)
    before_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    after_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    correlation_id: Mapped[str | None] = mapped_column(String(32), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class SystemEvent(UuidPkMixin, TimestampMixin, Base):
    """Operational event surfaced to administrators."""

    __tablename__ = "system_events"
    __table_args__ = (
        Index("ix_system_events_severity_created", "severity", "created_at"),
        Index("ix_system_events_component", "component"),
        Index("ix_system_events_correlation", "correlation_id"),
    )

    severity: Mapped[Severity] = mapped_column(
        SAEnum(Severity, native_enum=False, length=16), nullable=False
    )
    component: Mapped[str] = mapped_column(String(48), nullable=False)
    event_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    message: Mapped[str] = mapped_column(String(1024), nullable=False)
    context_json: Mapped[dict[str, Any] | None] = mapped_column(JsonDocument, default=None)
    correlation_id: Mapped[str | None] = mapped_column(String(32), default=None)
    portal_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidBinary, ForeignKey("portal_accounts.id", ondelete="SET NULL"), default=None
    )
    telegram_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidBinary, ForeignKey("telegram_users.id", ondelete="SET NULL"), default=None
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidBinary, ForeignKey("telegram_users.id", ondelete="SET NULL"), default=None
    )
    resolution_note: Mapped[str | None] = mapped_column(String(512), default=None)


class WorkerHeartbeat(UuidPkMixin, TimestampMixin, Base):
    """Liveness record published by every worker process."""

    __tablename__ = "worker_heartbeats"
    __table_args__ = (UniqueConstraint("worker_id"),)

    worker_id: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    app_version: Mapped[str] = mapped_column(String(32), nullable=False)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JsonDocument, default=None)


class CircuitBreakerStateRecord(UuidPkMixin, TimestampMixin, Base):
    """Persisted circuit breaker, shared by every worker."""

    __tablename__ = "circuit_breakers"
    __table_args__ = (UniqueConstraint("scope", "subject_key"),)

    scope: Mapped[BreakerScope] = mapped_column(
        SAEnum(BreakerScope, native_enum=False, length=16), nullable=False
    )
    #: ``GLOBAL`` for the global breaker, otherwise the portal account id hex.
    subject_key: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[BreakerState] = mapped_column(
        SAEnum(BreakerState, native_enum=False, length=16),
        default=BreakerState.CLOSED,
        nullable=False,
    )
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    reopen_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    last_failure_code: Mapped[str | None] = mapped_column(String(64), default=None)
    half_open_token: Mapped[bytes | None] = mapped_column(BINARY(16), default=None)
    overridden_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidBinary, ForeignKey("telegram_users.id", ondelete="SET NULL"), default=None
    )
    override_reason: Mapped[str | None] = mapped_column(String(255), default=None)


class PortalCallLog(UuidPkMixin, Base):
    """Rolling window of portal calls used for global rate limiting and metrics."""

    __tablename__ = "portal_call_logs"
    __table_args__ = (Index("ix_portal_call_logs_created", "created_at"),)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    portal_account_id: Mapped[uuid.UUID | None] = mapped_column(UuidBinary, default=None)
    operation: Mapped[str] = mapped_column(String(48), nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, default=None)
    duration_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)


class BackupStatus(UuidPkMixin, TimestampMixin, Base):
    """Latest known state of the encrypted database backup job."""

    __tablename__ = "backup_status"

    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    artifact_name: Mapped[str | None] = mapped_column(String(255), default=None)
    size_bytes: Mapped[int | None] = mapped_column(Integer, default=None)
    schema_version: Mapped[str | None] = mapped_column(String(64), default=None)
    error_detail: Mapped[str | None] = mapped_column(String(512), default=None)
