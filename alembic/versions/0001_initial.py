"""Initial schema.

Creates the whole database: users and invites, portal accounts and patients,
searches, watches and their filters, booking and cancellation attempts,
appointments, notifications, and the operational tables that replace Redis
(leases, heartbeats, circuit breakers, rate limits, confirmation nonces and
callback consumptions).

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.db.types

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backup_status",
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("artifact_name", sa.String(length=255), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("schema_version", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.String(length=512), nullable=True),
        sa.Column("id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_backup_status")),
    )
    op.create_table(
        "connect_rate_limits",
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("subject_hash", sa.BINARY(length=32), nullable=False),
        sa.Column("window_start", sa.DateTime(), nullable=False),
        sa.Column("counter", sa.Integer(), nullable=False),
        sa.Column("id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connect_rate_limits")),
        sa.UniqueConstraint(
            "scope",
            "subject_hash",
            "window_start",
            name=op.f("uq_connect_rate_limits_scope_subject_hash_window_start"),
        ),
    )
    op.create_index(
        op.f("ix_connect_rate_limits_window_start"),
        "connect_rate_limits",
        ["window_start"],
        unique=False,
    )
    op.create_table(
        "portal_call_logs",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("portal_account_id", app.db.types.UuidBinary(length=16), nullable=True),
        sa.Column("operation", sa.String(length=48), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_portal_call_logs")),
    )
    op.create_index("ix_portal_call_logs_created", "portal_call_logs", ["created_at"], unique=False)
    op.create_table(
        "telegram_users",
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username_ciphertext", app.db.types.Ciphertext(length=512), nullable=True),
        sa.Column("first_name_ciphertext", app.db.types.Ciphertext(length=512), nullable=True),
        sa.Column("last_name_ciphertext", app.db.types.Ciphertext(length=512), nullable=True),
        sa.Column(
            "role",
            sa.Enum("USER", "ADMIN", name="userrole", native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING_CONSENT",
                "ACTIVE",
                "SUSPENDED",
                "BLOCKED",
                "DELETED",
                name="userstatus",
                native_enum=False,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("notifications_enabled", sa.Boolean(), nullable=False),
        sa.Column("quiet_hours_start", sa.Time(), nullable=True),
        sa.Column("quiet_hours_end", sa.Time(), nullable=True),
        sa.Column("page_size", sa.Integer(), nullable=False),
        sa.Column("max_slots_shown", sa.Integer(), nullable=False),
        sa.Column("consent_version", sa.String(length=16), nullable=True),
        sa.Column("consented_at", sa.DateTime(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telegram_users")),
    )
    op.create_index(op.f("ix_telegram_users_status"), "telegram_users", ["status"], unique=False)
    op.create_index(
        op.f("ix_telegram_users_telegram_id"), "telegram_users", ["telegram_id"], unique=True
    )
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("app_version", sa.String(length=32), nullable=False),
        sa.Column("metadata_json", app.db.types.JsonDocument(), nullable=True),
        sa.Column("id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_worker_heartbeats")),
        sa.UniqueConstraint("worker_id", name=op.f("uq_worker_heartbeats_worker_id")),
    )
    op.create_index(
        op.f("ix_worker_heartbeats_last_seen_at"),
        "worker_heartbeats",
        ["last_seen_at"],
        unique=False,
    )
    op.create_table(
        "admin_audit_log",
        sa.Column("admin_user_id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column(
            "action",
            sa.Enum(
                "ADMIN_SESSION_START",
                "REVEAL_PII",
                "INVITE_CREATED",
                "INVITE_REVOKED",
                "USER_STATUS_CHANGED",
                "ACCOUNT_STATUS_CHANGED",
                "WATCH_STATUS_CHANGED",
                "WATCH_NOTIFICATIONS_CHANGED",
                "WATCH_RUN_NOW",
                "DELEGATED_BOOKING",
                "DELEGATED_CANCELLATION",
                "DELEGATED_PDF_DOWNLOAD",
                "BREAKER_OVERRIDE",
                "DATA_DELETED",
                "CONFIG_CHANGED",
                "SYSTEM_EVENT_RESOLVED",
                "FORCED_SYNC",
                name="auditaction",
                native_enum=False,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=512), nullable=True),
        sa.Column("before_hash", sa.String(length=64), nullable=True),
        sa.Column("after_hash", sa.String(length=64), nullable=True),
        sa.Column("correlation_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.ForeignKeyConstraint(
            ["admin_user_id"],
            ["telegram_users.id"],
            name=op.f("fk_admin_audit_log_admin_user_id_telegram_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_audit_log")),
    )
    op.create_index(
        "ix_admin_audit_log_admin_created",
        "admin_audit_log",
        ["admin_user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_admin_audit_log_correlation", "admin_audit_log", ["correlation_id"], unique=False
    )
    op.create_index(
        "ix_admin_audit_log_target", "admin_audit_log", ["target_type", "target_id"], unique=False
    )
    op.create_table(
        "callback_consumptions",
        sa.Column("dedup_key", sa.BINARY(length=32), nullable=False),
        sa.Column("telegram_user_id", app.db.types.UuidBinary(length=16), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["telegram_users.id"],
            name=op.f("fk_callback_consumptions_telegram_user_id_telegram_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_callback_consumptions")),
        sa.UniqueConstraint("dedup_key", name=op.f("uq_callback_consumptions_dedup_key")),
    )
    op.create_index(
        op.f("ix_callback_consumptions_expires_at"),
        "callback_consumptions",
        ["expires_at"],
        unique=False,
    )
    op.create_table(
        "circuit_breakers",
        sa.Column(
            "scope",
            sa.Enum("GLOBAL", "ACCOUNT", name="breakerscope", native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column("subject_key", sa.String(length=64), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "CLOSED", "OPEN", "HALF_OPEN", name="breakerstate", native_enum=False, length=16
            ),
            nullable=False,
        ),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("opened_at", sa.DateTime(), nullable=True),
        sa.Column("reopen_at", sa.DateTime(), nullable=True),
        sa.Column("last_failure_code", sa.String(length=64), nullable=True),
        sa.Column("half_open_probe", sa.BINARY(length=16), nullable=True),
        sa.Column("overridden_by_user_id", app.db.types.UuidBinary(length=16), nullable=True),
        sa.Column("override_reason", sa.String(length=255), nullable=True),
        sa.Column("id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["overridden_by_user_id"],
            ["telegram_users.id"],
            name=op.f("fk_circuit_breakers_overridden_by_user_id_telegram_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_circuit_breakers")),
        sa.UniqueConstraint(
            "scope", "subject_key", name=op.f("uq_circuit_breakers_scope_subject_key")
        ),
    )
    op.create_table(
        "confirmation_nonces",
        sa.Column("telegram_user_id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column(
            "action_type",
            sa.Enum(
                "BOOK_SLOT",
                "CANCEL_APPOINTMENT",
                "DELETE_WATCH",
                "DELETE_ACCOUNT",
                "DELETE_PROFILE",
                "ENABLE_AUTOBOOK",
                "ADMIN_BLOCK_USER",
                "ADMIN_DELEGATED_BOOK",
                "ADMIN_DELEGATED_CANCEL",
                "ADMIN_REVEAL_PII",
                "ADMIN_BREAKER_OVERRIDE",
                "ADMIN_REVOKE_INVITE",
                "ADMIN_DELETE_USER_DATA",
                name="confirmationaction",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("nonce_hash", sa.BINARY(length=32), nullable=False),
        sa.Column("context_hash", sa.BINARY(length=32), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["telegram_users.id"],
            name=op.f("fk_confirmation_nonces_telegram_user_id_telegram_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_confirmation_nonces")),
        sa.UniqueConstraint("nonce_hash", name=op.f("uq_confirmation_nonces_nonce_hash")),
    )
    op.create_index(
        op.f("ix_confirmation_nonces_expires_at"),
        "confirmation_nonces",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_confirmation_nonces_user_action",
        "confirmation_nonces",
        ["telegram_user_id", "action_type"],
        unique=False,
    )
    op.create_table(
        "conversation_states",
        sa.Column("telegram_user_id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("state", sa.String(length=64), nullable=False),
        sa.Column("payload_ciphertext", app.db.types.Ciphertext(length=16384), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["telegram_users.id"],
            name=op.f("fk_conversation_states_telegram_user_id_telegram_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("telegram_user_id", name=op.f("pk_conversation_states")),
    )
    op.create_index(
        op.f("ix_conversation_states_expires_at"),
        "conversation_states",
        ["expires_at"],
        unique=False,
    )
    op.create_table(
        "invite_codes",
        sa.Column("code_hash", sa.BINARY(length=32), nullable=False),
        sa.Column("created_by_user_id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=False),
        sa.Column("used_count", sa.Integer(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["telegram_users.id"],
            name=op.f("fk_invite_codes_created_by_user_id_telegram_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invite_codes")),
        sa.UniqueConstraint("code_hash", name=op.f("uq_invite_codes_code_hash")),
    )
    op.create_index(
        op.f("ix_invite_codes_expires_at"), "invite_codes", ["expires_at"], unique=False
    )
    op.create_table(
        "portal_accounts",
        sa.Column("telegram_user_id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("email_ciphertext", app.db.types.Ciphertext(length=1024), nullable=True),
        sa.Column("email_blind_index", app.db.types.BlindIndexColumn(length=32), nullable=False),
        sa.Column("password_ciphertext", app.db.types.Ciphertext(length=2048), nullable=True),
        sa.Column("cookie_jar_ciphertext", app.db.types.Ciphertext(length=16384), nullable=True),
        sa.Column(
            "portal_metadata_ciphertext", app.db.types.Ciphertext(length=8192), nullable=True
        ),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "DISABLED",
                "REAUTH_REQUIRED",
                "LOCKED",
                "DELETED",
                name="portalaccountstatus",
                native_enum=False,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_at", sa.DateTime(), nullable=True),
        sa.Column("consecutive_auth_failures", sa.Integer(), nullable=False),
        sa.Column("encryption_key_version", sa.Integer(), nullable=False),
        sa.Column("deleted_marker", sa.BINARY(length=16), nullable=False),
        sa.Column("id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["telegram_users.id"],
            name=op.f("fk_portal_accounts_telegram_user_id_telegram_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_portal_accounts")),
        sa.UniqueConstraint(
            "telegram_user_id",
            "email_blind_index",
            "deleted_marker",
            name="uq_portal_accounts_user_email_alive",
        ),
    )
    op.create_index(
        op.f("ix_portal_accounts_email_blind_index"),
        "portal_accounts",
        ["email_blind_index"],
        unique=False,
    )
    op.create_index("ix_portal_accounts_status", "portal_accounts", ["status"], unique=False)
    op.create_index(
        op.f("ix_portal_accounts_telegram_user_id"),
        "portal_accounts",
        ["telegram_user_id"],
        unique=False,
    )
    op.create_table(
        "user_settings_audit",
        sa.Column("telegram_user_id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("setting", sa.String(length=64), nullable=False),
        sa.Column("old_value", sa.String(length=128), nullable=True),
        sa.Column("new_value", sa.String(length=128), nullable=True),
        sa.Column("id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["telegram_users.id"],
            name=op.f("fk_user_settings_audit_telegram_user_id_telegram_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_settings_audit")),
    )
    op.create_index(
        op.f("ix_user_settings_audit_telegram_user_id"),
        "user_settings_audit",
        ["telegram_user_id"],
        unique=False,
    )
    op.create_table(
        "invite_redemptions",
        sa.Column("invite_id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("telegram_user_id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(), nullable=False),
        sa.Column("id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["invite_id"],
            ["invite_codes.id"],
            name=op.f("fk_invite_redemptions_invite_id_invite_codes"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["telegram_users.id"],
            name=op.f("fk_invite_redemptions_telegram_user_id_telegram_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invite_redemptions")),
        sa.UniqueConstraint(
            "invite_id",
            "telegram_user_id",
            name=op.f("uq_invite_redemptions_invite_id_telegram_user_id"),
        ),
    )
    op.create_table(
        "patients",
        sa.Column("portal_account_id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column(
            "external_tax_code_ciphertext", app.db.types.Ciphertext(length=512), nullable=False
        ),
        sa.Column(
            "external_tax_code_blind_index",
            app.db.types.BlindIndexColumn(length=32),
            nullable=False,
        ),
        sa.Column("display_name_ciphertext", app.db.types.Ciphertext(length=1024), nullable=True),
        sa.Column("relationship_ciphertext", app.db.types.Ciphertext(length=512), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["portal_account_id"],
            ["portal_accounts.id"],
            name=op.f("fk_patients_portal_account_id_portal_accounts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_patients")),
        sa.UniqueConstraint(
            "portal_account_id",
            "external_tax_code_blind_index",
            name="uq_patients_account_tax_code",
        ),
    )
    op.create_index(
        op.f("ix_patients_portal_account_id"), "patients", ["portal_account_id"], unique=False
    )
    op.create_table(
        "system_events",
        sa.Column(
            "severity",
            sa.Enum(
                "INFO",
                "WARNING",
                "ERROR",
                "CRITICAL",
                name="severity",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("component", sa.String(length=48), nullable=False),
        sa.Column("event_code", sa.String(length=64), nullable=False),
        sa.Column("message", sa.String(length=1024), nullable=False),
        sa.Column("context_json", app.db.types.JsonDocument(), nullable=True),
        sa.Column("correlation_id", sa.String(length=32), nullable=True),
        sa.Column("portal_account_id", app.db.types.UuidBinary(length=16), nullable=True),
        sa.Column("telegram_user_id", app.db.types.UuidBinary(length=16), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_by_user_id", app.db.types.UuidBinary(length=16), nullable=True),
        sa.Column("resolution_note", sa.String(length=512), nullable=True),
        sa.Column("id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["portal_account_id"],
            ["portal_accounts.id"],
            name=op.f("fk_system_events_portal_account_id_portal_accounts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_user_id"],
            ["telegram_users.id"],
            name=op.f("fk_system_events_resolved_by_user_id_telegram_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["telegram_users.id"],
            name=op.f("fk_system_events_telegram_user_id_telegram_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_system_events")),
    )
    op.create_index("ix_system_events_component", "system_events", ["component"], unique=False)
    op.create_index(
        "ix_system_events_correlation", "system_events", ["correlation_id"], unique=False
    )
    op.create_index(
        op.f("ix_system_events_event_code"), "system_events", ["event_code"], unique=False
    )
    op.create_index(
        "ix_system_events_severity_created",
        "system_events",
        ["severity", "created_at"],
        unique=False,
    )
    op.create_table(
        "appointments_cache",
        sa.Column("telegram_user_id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("portal_account_id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("patient_id", app.db.types.UuidBinary(length=16), nullable=True),
        sa.Column("portal_external_id", sa.String(length=64), nullable=False),
        sa.Column("service_name_ciphertext", app.db.types.Ciphertext(length=1024), nullable=True),
        sa.Column("doctor_name_ciphertext", app.db.types.Ciphertext(length=1024), nullable=True),
        sa.Column("location_name_ciphertext", app.db.types.Ciphertext(length=1024), nullable=True),
        sa.Column("patient_label_ciphertext", app.db.types.Ciphertext(length=1024), nullable=True),
        sa.Column("scheduled_at_utc", sa.DateTime(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "CANCELLED",
                "COMPLETED",
                "UNKNOWN",
                name="appointmentstatus",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("payment_note", sa.String(length=255), nullable=True),
        sa.Column("pdf_available", sa.Boolean(), nullable=False),
        sa.Column("cancel_path", sa.String(length=255), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column("raw_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("cancellation_attempts", sa.Integer(), nullable=False),
        sa.Column("id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            name=op.f("fk_appointments_cache_patient_id_patients"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["portal_account_id"],
            ["portal_accounts.id"],
            name=op.f("fk_appointments_cache_portal_account_id_portal_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["telegram_users.id"],
            name=op.f("fk_appointments_cache_telegram_user_id_telegram_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_appointments_cache")),
        sa.UniqueConstraint(
            "portal_account_id",
            "portal_external_id",
            name=op.f("uq_appointments_cache_portal_account_id_portal_external_id"),
        ),
    )
    op.create_index("ix_appointments_cache_status", "appointments_cache", ["status"], unique=False)
    op.create_index(
        "ix_appointments_cache_user_scheduled",
        "appointments_cache",
        ["telegram_user_id", "scheduled_at_utc"],
        unique=False,
    )
    op.create_table(
        "booking_drafts",
        sa.Column("telegram_user_id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("portal_account_id", app.db.types.UuidBinary(length=16), nullable=True),
        sa.Column("patient_id", app.db.types.UuidBinary(length=16), nullable=True),
        sa.Column(
            "search_type",
            sa.Enum("SERVICE", "DOCTOR", name="searchtype", native_enum=False, length=16),
            nullable=True,
        ),
        sa.Column("query_text", sa.String(length=160), nullable=True),
        sa.Column("selected_service_external_id", sa.String(length=64), nullable=True),
        sa.Column("selected_service_name", sa.String(length=255), nullable=True),
        sa.Column("selected_specification_id", sa.String(length=64), nullable=True),
        sa.Column("selected_doctor_external_id", sa.String(length=128), nullable=True),
        sa.Column("selected_doctor_name", sa.String(length=255), nullable=True),
        sa.Column("service_online_bookable", sa.Boolean(), nullable=False),
        sa.Column("preparation_note", sa.String(length=1024), nullable=True),
        sa.Column(
            "location_mode",
            sa.Enum("ALL_DYNAMIC", "SELECTED", name="locationmode", native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column("selected_locations_json", app.db.types.JsonDocument(), nullable=False),
        sa.Column("available_locations_json", app.db.types.JsonDocument(), nullable=False),
        sa.Column(
            "date_mode",
            sa.Enum("FIXED", "ROLLING", name="datemode", native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column("date_from", sa.Date(), nullable=True),
        sa.Column("date_to", sa.Date(), nullable=True),
        sa.Column("rolling_days", sa.Integer(), nullable=True),
        sa.Column("min_lead_hours", sa.Integer(), nullable=False),
        sa.Column("weekdays_mask", sa.Integer(), nullable=False),
        sa.Column("time_windows_json", app.db.types.JsonDocument(), nullable=False),
        sa.Column("excluded_dates_json", app.db.types.JsonDocument(), nullable=False),
        sa.Column(
            "sort_strategy",
            sa.Enum(
                "EARLIEST",
                "PREFERRED_LOCATION_FIRST",
                "DATE_TIME_ASC",
                name="sortstrategy",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("selected_slot_ciphertext", app.db.types.Ciphertext(length=8192), nullable=True),
        sa.Column("slot_cache_ciphertext", app.db.types.Ciphertext(length=16777215), nullable=True),
        sa.Column("slot_cache_refreshed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "OPEN",
                "CONFIRMED",
                "ABANDONED",
                "EXPIRED",
                name="bookingdraftstatus",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("origin_watch_id", app.db.types.UuidBinary(length=16), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            name=op.f("fk_booking_drafts_patient_id_patients"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["portal_account_id"],
            ["portal_accounts.id"],
            name=op.f("fk_booking_drafts_portal_account_id_portal_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["telegram_users.id"],
            name=op.f("fk_booking_drafts_telegram_user_id_telegram_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_booking_drafts")),
    )
    op.create_index(
        op.f("ix_booking_drafts_expires_at"), "booking_drafts", ["expires_at"], unique=False
    )
    op.create_index(
        "ix_booking_drafts_user_status",
        "booking_drafts",
        ["telegram_user_id", "status"],
        unique=False,
    )
    op.create_table(
        "booking_watches",
        sa.Column("telegram_user_id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("portal_account_id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("patient_id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column(
            "search_type",
            sa.Enum("SERVICE", "DOCTOR", name="searchtype", native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column("query_text", sa.String(length=160), nullable=False),
        sa.Column("selected_service_external_id", sa.String(length=64), nullable=True),
        sa.Column("selected_service_name", sa.String(length=255), nullable=True),
        sa.Column("selected_specification_id", sa.String(length=64), nullable=True),
        sa.Column("selected_doctor_external_id", sa.String(length=128), nullable=True),
        sa.Column("selected_doctor_name", sa.String(length=255), nullable=True),
        sa.Column(
            "location_mode",
            sa.Enum("ALL_DYNAMIC", "SELECTED", name="locationmode", native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column(
            "date_mode",
            sa.Enum("FIXED", "ROLLING", name="datemode", native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column("date_from", sa.Date(), nullable=True),
        sa.Column("date_to", sa.Date(), nullable=True),
        sa.Column("rolling_days", sa.Integer(), nullable=True),
        sa.Column("min_lead_hours", sa.Integer(), nullable=False),
        sa.Column("weekdays_mask", sa.Integer(), nullable=False),
        sa.Column("time_windows_json", app.db.types.JsonDocument(), nullable=False),
        sa.Column("excluded_dates_json", app.db.types.JsonDocument(), nullable=False),
        sa.Column(
            "sort_strategy",
            sa.Enum(
                "EARLIEST",
                "PREFERRED_LOCATION_FIRST",
                "DATE_TIME_ASC",
                name="sortstrategy",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("max_results", sa.Integer(), nullable=False),
        sa.Column(
            "mode",
            sa.Enum(
                "NOTIFY",
                "AUTOBOOK",
                "AUTOBOOK_NOTIFY",
                name="watchmode",
                native_enum=False,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column("notifications_enabled", sa.Boolean(), nullable=False),
        sa.Column("notify_only_changes", sa.Boolean(), nullable=False),
        sa.Column("quiet_hours_start", sa.Time(), nullable=True),
        sa.Column("quiet_hours_end", sa.Time(), nullable=True),
        sa.Column(
            "completion_policy",
            sa.Enum(
                "COMPLETE_ON_SUCCESS",
                "CONTINUE_AFTER_SUCCESS",
                name="watchcompletionpolicy",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT",
                "ACTIVE",
                "PAUSED",
                "COMPLETED",
                "ERROR",
                "DELETED",
                name="watchstatus",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("next_check_at", sa.DateTime(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("last_run_now_at", sa.DateTime(), nullable=True),
        sa.Column("last_slot_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("consecutive_errors", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_at", sa.DateTime(), nullable=True),
        sa.Column("missing_location_reported_at", sa.DateTime(), nullable=True),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("completed_reason", sa.String(length=64), nullable=True),
        sa.Column("id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            name=op.f("fk_booking_watches_patient_id_patients"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["portal_account_id"],
            ["portal_accounts.id"],
            name=op.f("fk_booking_watches_portal_account_id_portal_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["telegram_users.id"],
            name=op.f("fk_booking_watches_telegram_user_id_telegram_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_booking_watches")),
    )
    op.create_index(
        "ix_booking_watches_account_status",
        "booking_watches",
        ["portal_account_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_booking_watches_due", "booking_watches", ["status", "next_check_at"], unique=False
    )
    op.create_index(
        "ix_booking_watches_user_status",
        "booking_watches",
        ["telegram_user_id", "status"],
        unique=False,
    )
    op.create_table(
        "booking_attempts",
        sa.Column("telegram_user_id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("portal_account_id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("patient_id", app.db.types.UuidBinary(length=16), nullable=True),
        sa.Column("watch_id", app.db.types.UuidBinary(length=16), nullable=True),
        sa.Column("draft_id", app.db.types.UuidBinary(length=16), nullable=True),
        sa.Column("initiated_by_admin_id", app.db.types.UuidBinary(length=16), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("slot_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("watch_slot_success_key", sa.String(length=96), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "STARTED",
                "VERIFYING",
                "SUCCEEDED",
                "SLOT_LOST",
                "REJECTED",
                "AMBIGUOUS",
                "FAILED",
                name="bookingattemptstatus",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column(
            "stage",
            sa.Enum(
                "CREATED",
                "SESSION_READY",
                "PATIENT_SELECTED",
                "SEARCH_SUBMITTED",
                "AVAILABILITY_REFRESHED",
                "SLOT_SELECTED",
                "CONFIRM_SENT",
                "RECONCILED",
                name="bookingstage",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("portal_appointment_external_id", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.String(length=512), nullable=True),
        sa.Column("correlation_id", sa.String(length=32), nullable=True),
        sa.Column("slot_starts_at", sa.DateTime(), nullable=True),
        sa.Column("location_name", sa.String(length=255), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("confirm_sent_at", sa.DateTime(), nullable=True),
        sa.Column("snapshot_before_hash", sa.BINARY(length=32), nullable=True),
        sa.Column("snapshot_after_hash", sa.BINARY(length=32), nullable=True),
        sa.Column("id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["booking_drafts.id"],
            name=op.f("fk_booking_attempts_draft_id_booking_drafts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["initiated_by_admin_id"],
            ["telegram_users.id"],
            name=op.f("fk_booking_attempts_initiated_by_admin_id_telegram_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            name=op.f("fk_booking_attempts_patient_id_patients"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["portal_account_id"],
            ["portal_accounts.id"],
            name=op.f("fk_booking_attempts_portal_account_id_portal_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["telegram_users.id"],
            name=op.f("fk_booking_attempts_telegram_user_id_telegram_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["watch_id"],
            ["booking_watches.id"],
            name=op.f("fk_booking_attempts_watch_id_booking_watches"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_booking_attempts")),
        sa.UniqueConstraint("idempotency_key", name="uq_booking_attempts_idempotency_key"),
        sa.UniqueConstraint(
            "watch_slot_success_key", name="uq_booking_attempts_watch_slot_success"
        ),
    )
    op.create_index(
        "ix_booking_attempts_account_status",
        "booking_attempts",
        ["portal_account_id", "status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_booking_attempts_slot_fingerprint"),
        "booking_attempts",
        ["slot_fingerprint"],
        unique=False,
    )
    op.create_index(
        "ix_booking_attempts_user_started",
        "booking_attempts",
        ["telegram_user_id", "started_at"],
        unique=False,
    )
    op.create_table(
        "cancellation_attempts",
        sa.Column("telegram_user_id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("portal_account_id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("appointment_id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("initiated_by_admin_id", app.db.types.UuidBinary(length=16), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.String(length=512), nullable=True),
        sa.Column("correlation_id", sa.String(length=32), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["appointments_cache.id"],
            name=op.f("fk_cancellation_attempts_appointment_id_appointments_cache"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["initiated_by_admin_id"],
            ["telegram_users.id"],
            name=op.f("fk_cancellation_attempts_initiated_by_admin_id_telegram_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["portal_account_id"],
            ["portal_accounts.id"],
            name=op.f("fk_cancellation_attempts_portal_account_id_portal_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["telegram_users.id"],
            name=op.f("fk_cancellation_attempts_telegram_user_id_telegram_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cancellation_attempts")),
        sa.UniqueConstraint(
            "idempotency_key", name=op.f("uq_cancellation_attempts_idempotency_key")
        ),
    )
    op.create_table(
        "seen_slots",
        sa.Column("watch_id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("slot_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_notified_at", sa.DateTime(), nullable=True),
        sa.Column("currently_available", sa.Boolean(), nullable=False),
        sa.Column("slot_starts_at", sa.DateTime(), nullable=True),
        sa.Column("location_id", sa.String(length=64), nullable=True),
        sa.Column("location_name", sa.String(length=255), nullable=True),
        sa.Column("id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.ForeignKeyConstraint(
            ["watch_id"],
            ["booking_watches.id"],
            name=op.f("fk_seen_slots_watch_id_booking_watches"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_seen_slots")),
        sa.UniqueConstraint(
            "watch_id", "slot_fingerprint", name=op.f("uq_seen_slots_watch_id_slot_fingerprint")
        ),
    )
    op.create_index(
        op.f("ix_seen_slots_last_seen_at"), "seen_slots", ["last_seen_at"], unique=False
    )
    op.create_index(
        "ix_seen_slots_watch_available",
        "seen_slots",
        ["watch_id", "currently_available"],
        unique=False,
    )
    op.create_table(
        "watch_locations",
        sa.Column("watch_id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("portal_location_id", sa.String(length=64), nullable=False),
        sa.Column("location_name", sa.String(length=255), nullable=False),
        sa.Column("preference_order", sa.Integer(), nullable=False),
        sa.Column("id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["watch_id"],
            ["booking_watches.id"],
            name=op.f("fk_watch_locations_watch_id_booking_watches"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_watch_locations")),
        sa.UniqueConstraint(
            "watch_id",
            "portal_location_id",
            name=op.f("uq_watch_locations_watch_id_portal_location_id"),
        ),
    )
    op.create_table(
        "watch_run_logs",
        sa.Column("watch_id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("slots_found", sa.Integer(), nullable=False),
        sa.Column("new_slots", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("correlation_id", sa.String(length=32), nullable=True),
        sa.Column("worker_id", sa.String(length=64), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.ForeignKeyConstraint(
            ["watch_id"],
            ["booking_watches.id"],
            name=op.f("fk_watch_run_logs_watch_id_booking_watches"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_watch_run_logs")),
    )
    op.create_index(
        "ix_watch_run_logs_watch_started",
        "watch_run_logs",
        ["watch_id", "started_at"],
        unique=False,
    )
    op.create_table(
        "notification_events",
        sa.Column("telegram_user_id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("watch_id", app.db.types.UuidBinary(length=16), nullable=True),
        sa.Column("appointment_id", app.db.types.UuidBinary(length=16), nullable=True),
        sa.Column("portal_account_id", app.db.types.UuidBinary(length=16), nullable=True),
        sa.Column("booking_attempt_id", app.db.types.UuidBinary(length=16), nullable=True),
        sa.Column(
            "event_type",
            sa.Enum(
                "NEW_SLOT",
                "SLOT_LOST",
                "AUTOBOOK_SUCCESS",
                "AUTOBOOK_FAILED",
                "AUTOBOOK_AMBIGUOUS",
                "REAUTH_REQUIRED",
                "WATCH_SUSPENDED",
                "APPOINTMENT_DETECTED",
                "CANCELLATION_SUCCESS",
                "CANCELLATION_FAILED",
                "PDF_UNAVAILABLE",
                "WATCH_AMBIGUOUS_SEARCH",
                "LOCATION_DISAPPEARED",
                "ADMIN_ACTION",
                name="notificationeventtype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("dedup_key", sa.BINARY(length=32), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "SENT",
                "FAILED",
                "SUPPRESSED",
                name="notificationstatus",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("payload_ciphertext", app.db.types.Ciphertext(length=16384), nullable=True),
        sa.Column("telegram_message_id", sa.Integer(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("suppressed_reason", sa.String(length=64), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("correlation_id", sa.String(length=32), nullable=True),
        sa.Column("id", app.db.types.UuidBinary(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["appointments_cache.id"],
            name=op.f("fk_notification_events_appointment_id_appointments_cache"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["booking_attempt_id"],
            ["booking_attempts.id"],
            name=op.f("fk_notification_events_booking_attempt_id_booking_attempts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["portal_account_id"],
            ["portal_accounts.id"],
            name=op.f("fk_notification_events_portal_account_id_portal_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["telegram_users.id"],
            name=op.f("fk_notification_events_telegram_user_id_telegram_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["watch_id"],
            ["booking_watches.id"],
            name=op.f("fk_notification_events_watch_id_booking_watches"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_events")),
    )
    op.create_index(
        "ix_notification_events_dedup", "notification_events", ["dedup_key"], unique=True
    )
    op.create_index(
        "ix_notification_events_pending",
        "notification_events",
        ["status", "next_attempt_at"],
        unique=False,
    )
    op.create_index(
        "ix_notification_events_user_created",
        "notification_events",
        ["telegram_user_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    # Dropping a table removes its indexes as well, and the reverse creation
    # order removes every child before the row it references, so no foreign
    # key is left dangling.
    op.drop_table("notification_events")
    op.drop_table("watch_run_logs")
    op.drop_table("watch_locations")
    op.drop_table("seen_slots")
    op.drop_table("cancellation_attempts")
    op.drop_table("booking_attempts")
    op.drop_table("booking_watches")
    op.drop_table("booking_drafts")
    op.drop_table("appointments_cache")
    op.drop_table("system_events")
    op.drop_table("patients")
    op.drop_table("invite_redemptions")
    op.drop_table("user_settings_audit")
    op.drop_table("portal_accounts")
    op.drop_table("invite_codes")
    op.drop_table("conversation_states")
    op.drop_table("confirmation_nonces")
    op.drop_table("circuit_breakers")
    op.drop_table("callback_consumptions")
    op.drop_table("admin_audit_log")
    op.drop_table("worker_heartbeats")
    op.drop_table("telegram_users")
    op.drop_table("portal_call_logs")
    op.drop_table("connect_rate_limits")
    op.drop_table("backup_status")
