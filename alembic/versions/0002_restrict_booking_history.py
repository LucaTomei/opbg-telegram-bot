"""Protect the booking history from cascading deletes.

Booking and cancellation attempts record a mutating action performed on the
portal. They must never disappear because their owner or their portal account
row was deleted, so their foreign keys are switched from CASCADE to RESTRICT.
Accounts and users are soft deleted by the application, so nothing legitimate
relies on the previous behaviour.

Revision ID: 0002_restrict_booking_history
Revises: 0001_initial
Create Date: 2026-08-24
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_restrict_booking_history"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("fk_booking_attempts_portal_account_id_portal_accounts"),
        "booking_attempts",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_booking_attempts_telegram_user_id_telegram_users"),
        "booking_attempts",
        type_="foreignkey",
    )
    op.create_foreign_key(
        op.f("fk_booking_attempts_portal_account_id_portal_accounts"),
        "booking_attempts",
        "portal_accounts",
        ["portal_account_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_booking_attempts_telegram_user_id_telegram_users"),
        "booking_attempts",
        "telegram_users",
        ["telegram_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        op.f("fk_cancellation_attempts_portal_account_id_portal_accounts"),
        "cancellation_attempts",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_cancellation_attempts_telegram_user_id_telegram_users"),
        "cancellation_attempts",
        type_="foreignkey",
    )
    op.create_foreign_key(
        op.f("fk_cancellation_attempts_portal_account_id_portal_accounts"),
        "cancellation_attempts",
        "portal_accounts",
        ["portal_account_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_cancellation_attempts_telegram_user_id_telegram_users"),
        "cancellation_attempts",
        "telegram_users",
        ["telegram_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_cancellation_attempts_telegram_user_id_telegram_users"),
        "cancellation_attempts",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_cancellation_attempts_portal_account_id_portal_accounts"),
        "cancellation_attempts",
        type_="foreignkey",
    )
    op.create_foreign_key(
        op.f("fk_cancellation_attempts_telegram_user_id_telegram_users"),
        "cancellation_attempts",
        "telegram_users",
        ["telegram_user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("fk_cancellation_attempts_portal_account_id_portal_accounts"),
        "cancellation_attempts",
        "portal_accounts",
        ["portal_account_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        op.f("fk_booking_attempts_telegram_user_id_telegram_users"),
        "booking_attempts",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_booking_attempts_portal_account_id_portal_accounts"),
        "booking_attempts",
        type_="foreignkey",
    )
    op.create_foreign_key(
        op.f("fk_booking_attempts_telegram_user_id_telegram_users"),
        "booking_attempts",
        "telegram_users",
        ["telegram_user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("fk_booking_attempts_portal_account_id_portal_accounts"),
        "booking_attempts",
        "portal_accounts",
        ["portal_account_id"],
        ["id"],
        ondelete="CASCADE",
    )
