"""Drop the unused lease column and clarify the breaker probe.

``watch_run_logs.lease_token`` was never written: the lease lives on
``booking_watches``. ``circuit_breakers.half_open_token`` is renamed to
``half_open_probe`` because it marks the single request allowed while the
breaker is half open and is not a credential.

Revision ID: 0003_clean_internal_columns
Revises: 0002_restrict_booking_history
Create Date: 2026-08-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_clean_internal_columns"
down_revision: str | None = "0002_restrict_booking_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "circuit_breakers", sa.Column("half_open_probe", sa.BINARY(length=16), nullable=True)
    )
    op.drop_column("circuit_breakers", "half_open_token")
    op.drop_column("watch_run_logs", "lease_token")


def downgrade() -> None:
    op.add_column("watch_run_logs", sa.Column("lease_token", sa.BINARY(length=16), nullable=True))
    op.add_column(
        "circuit_breakers", sa.Column("half_open_token", sa.BINARY(length=16), nullable=True)
    )
    op.drop_column("circuit_breakers", "half_open_probe")
