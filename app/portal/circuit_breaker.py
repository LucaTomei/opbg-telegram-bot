"""Database backed circuit breaker for portal traffic.

A breaker exists per portal account and one for the whole portal.  Opening a
breaker suspends only the affected scope, and reopening goes through a
half-open state where a single idempotent request decides the outcome.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.system import CircuitBreakerStateRecord
from app.domain.enums import BreakerScope, BreakerState
from app.domain.errors import CircuitOpenError
from app.logging import get_logger

logger = get_logger(__name__)

GLOBAL_SUBJECT = "GLOBAL"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


@dataclass(frozen=True, slots=True)
class BreakerPolicy:
    account_failure_threshold: int = 5
    account_cooldown_seconds: int = 900
    global_failure_threshold: int = 20
    global_cooldown_seconds: int = 600


class CircuitBreakerService:
    """Reads and updates persisted breaker state."""

    def __init__(self, policy: BreakerPolicy) -> None:
        self._policy = policy

    def _threshold(self, scope: BreakerScope) -> int:
        return (
            self._policy.global_failure_threshold
            if scope is BreakerScope.GLOBAL
            else self._policy.account_failure_threshold
        )

    def _cooldown(self, scope: BreakerScope) -> int:
        return (
            self._policy.global_cooldown_seconds
            if scope is BreakerScope.GLOBAL
            else self._policy.account_cooldown_seconds
        )

    async def _load(
        self, session: AsyncSession, scope: BreakerScope, subject: str
    ) -> CircuitBreakerStateRecord:
        record = await session.scalar(
            select(CircuitBreakerStateRecord).where(
                CircuitBreakerStateRecord.scope == scope,
                CircuitBreakerStateRecord.subject_key == subject,
            )
        )
        if record is not None:
            return record
        record = CircuitBreakerStateRecord(scope=scope, subject_key=subject)
        try:
            # A savepoint keeps a concurrent insert from discarding the rest of
            # the transaction.
            async with session.begin_nested():
                session.add(record)
                await session.flush()
        except IntegrityError:
            existing = await session.scalar(
                select(CircuitBreakerStateRecord).where(
                    CircuitBreakerStateRecord.scope == scope,
                    CircuitBreakerStateRecord.subject_key == subject,
                )
            )
            if existing is None:  # pragma: no cover - defensive
                raise
            return existing
        return record

    async def ensure_closed(
        self, session: AsyncSession, *, scope: BreakerScope, subject: str = GLOBAL_SUBJECT
    ) -> None:
        """Raise :class:`CircuitOpenError` when the breaker forbids traffic."""
        record = await self._load(session, scope, subject)
        now = _now()
        if record.state is BreakerState.CLOSED:
            return
        if record.state is BreakerState.OPEN:
            if record.reopen_at is not None and record.reopen_at <= now:
                record.state = BreakerState.HALF_OPEN
                record.half_open_token = os.urandom(16)
                await session.flush()
                return
            remaining = 0.0
            if record.reopen_at is not None:
                remaining = max((record.reopen_at - now).total_seconds(), 0.0)
            raise CircuitOpenError(
                "portal circuit breaker is open",
                retry_after_seconds=remaining,
                scope=scope.value,
            )
        # HALF_OPEN: exactly one probe is allowed at a time.
        if record.half_open_token is None:
            record.half_open_token = os.urandom(16)
            await session.flush()
            return

    async def record_success(
        self, session: AsyncSession, *, scope: BreakerScope, subject: str = GLOBAL_SUBJECT
    ) -> None:
        record = await self._load(session, scope, subject)
        record.state = BreakerState.CLOSED
        record.failure_count = 0
        record.opened_at = None
        record.reopen_at = None
        record.half_open_token = None
        record.last_failure_code = None
        await session.flush()

    async def record_failure(
        self,
        session: AsyncSession,
        *,
        scope: BreakerScope,
        subject: str = GLOBAL_SUBJECT,
        error_code: str,
    ) -> bool:
        """Register a failure and return ``True`` when the breaker opened."""
        record = await self._load(session, scope, subject)
        record.failure_count += 1
        record.last_failure_code = error_code[:64]
        threshold = self._threshold(scope)
        opened = False
        if record.state is BreakerState.HALF_OPEN or record.failure_count >= threshold:
            now = _now()
            record.state = BreakerState.OPEN
            record.opened_at = now
            record.reopen_at = now + timedelta(seconds=self._cooldown(scope))
            record.half_open_token = None
            opened = True
            logger.warning(
                "portal circuit breaker opened",
                extra={
                    "event_code": "BREAKER_OPENED",
                    "scope": scope.value,
                    "subject": subject,
                    "error_code": error_code,
                },
            )
        await session.flush()
        return opened

    async def force_state(
        self,
        session: AsyncSession,
        *,
        scope: BreakerScope,
        subject: str,
        state: BreakerState,
        admin_user_id: object,
        reason: str,
    ) -> CircuitBreakerStateRecord:
        """Administrative override; always paired with an audit entry."""
        record = await self._load(session, scope, subject)
        record.state = state
        record.override_reason = reason[:255]
        record.overridden_by_user_id = admin_user_id  # type: ignore[assignment]
        if state is BreakerState.CLOSED:
            record.failure_count = 0
            record.opened_at = None
            record.reopen_at = None
            record.half_open_token = None
        elif state is BreakerState.OPEN:
            now = _now()
            record.opened_at = now
            record.reopen_at = now + timedelta(seconds=self._cooldown(scope))
        await session.flush()
        return record

    async def list_states(self, session: AsyncSession) -> list[CircuitBreakerStateRecord]:
        result = await session.scalars(
            select(CircuitBreakerStateRecord).order_by(
                CircuitBreakerStateRecord.scope, CircuitBreakerStateRecord.subject_key
            )
        )
        return list(result)
