"""Logical locks used to serialise mutating portal sequences.

MySQL named locks give a cluster-wide mutex without introducing Redis.  A lock
is held for the whole booking or cancellation sequence of one portal account,
which guarantees that two monitors — or a monitor and an interactive user —
never confirm two bookings for the same account at the same time.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import named_lock

ACCOUNT_LOCK_PREFIX = "opbg_account"
APPOINTMENT_LOCK_PREFIX = "opbg_appointment"
WATCH_LOCK_PREFIX = "opbg_watch"


def account_lock_name(account_id: uuid.UUID) -> str:
    return f"{ACCOUNT_LOCK_PREFIX}_{account_id.hex}"


def appointment_lock_name(appointment_id: uuid.UUID) -> str:
    return f"{APPOINTMENT_LOCK_PREFIX}_{appointment_id.hex}"


@asynccontextmanager
async def account_lock(
    session: AsyncSession, account_id: uuid.UUID, *, timeout_seconds: int = 0
) -> AsyncIterator[None]:
    """Serialise every mutating sequence for one portal account."""
    async with named_lock(session, account_lock_name(account_id), timeout_seconds=timeout_seconds):
        yield


@asynccontextmanager
async def appointment_lock(
    session: AsyncSession, appointment_id: uuid.UUID, *, timeout_seconds: int = 0
) -> AsyncIterator[None]:
    """Prevent a repeated callback from cancelling the same appointment twice."""
    async with named_lock(
        session, appointment_lock_name(appointment_id), timeout_seconds=timeout_seconds
    ):
        yield
