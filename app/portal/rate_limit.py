"""Rate limiting for outbound portal traffic.

Two layers cooperate:

* an in-process limiter caps concurrency and enforces a minimum spacing between
  requests, both globally and per portal account;
* a database backed gate shares the global budget between the ``bot`` and
  ``worker`` containers using a MySQL named lock plus a sliding window over
  ``portal_call_logs``.  This keeps the deployment free of Redis while still
  preventing two processes from bursting at the same time.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import named_lock
from app.logging import get_logger

logger = get_logger(__name__)

GLOBAL_GATE_LOCK = "opbg_portal_gate"


@dataclass(slots=True)
class RateLimitPolicy:
    """Static rate limiting parameters."""

    max_concurrency: int = 2
    min_interval_seconds: float = 0.8
    per_account_min_interval_seconds: float = 1.5
    global_window_seconds: int = 60
    global_max_calls_per_window: int = 60


class AsyncRateLimiter:
    """In-process concurrency and spacing limiter."""

    def __init__(
        self,
        policy: RateLimitPolicy,
        *,
        jitter: Callable[[], float] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], object] | None = None,
    ) -> None:
        self._policy = policy
        self._semaphore = asyncio.Semaphore(policy.max_concurrency)
        self._last_call_at = 0.0
        self._last_account_call_at: dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._jitter = jitter or (lambda: 0.0)
        self._clock = clock
        self._sleep = sleep or asyncio.sleep

    async def _wait_turn(self, account_key: str | None) -> None:
        async with self._lock:
            now = self._clock()
            waits = [self._policy.min_interval_seconds - (now - self._last_call_at)]
            if account_key is not None:
                last = self._last_account_call_at.get(account_key, 0.0)
                waits.append(self._policy.per_account_min_interval_seconds - (now - last))
            delay = max([*waits, 0.0]) + self._jitter()
            if delay > 0:
                await self._sleep(delay)  # type: ignore[misc]
            stamped = self._clock()
            self._last_call_at = stamped
            if account_key is not None:
                self._last_account_call_at[account_key] = stamped

    @asynccontextmanager
    async def slot(self, *, account_key: str | None = None) -> AsyncIterator[None]:
        """Reserve one outbound request slot."""
        async with self._semaphore:
            await self._wait_turn(account_key)
            yield


class PortalGate(Protocol):
    """Cross-process gate protecting the global portal call budget."""

    async def acquire(self, *, operation: str, account_key: str | None) -> None: ...


class NullPortalGate:
    """Gate used when a single process owns all portal traffic (tests, CLI)."""

    async def acquire(self, *, operation: str, account_key: str | None) -> None:
        return None


class DatabasePortalGate:
    """Sliding-window global gate shared through MySQL."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        policy: RateLimitPolicy,
        *,
        sleep: Callable[[float], object] | None = None,
        max_waits: int = 20,
    ) -> None:
        self._session_factory = session_factory
        self._policy = policy
        self._sleep = sleep or asyncio.sleep
        self._max_waits = max_waits

    async def acquire(self, *, operation: str, account_key: str | None) -> None:
        for _ in range(self._max_waits):
            wait_for = await self._try_reserve(operation)
            if wait_for <= 0:
                return
            await self._sleep(min(wait_for, 5.0))  # type: ignore[misc]
        logger.warning(
            "portal gate saturated, proceeding after the maximum wait",
            extra={"event_code": "PORTAL_GATE_SATURATED", "operation": operation},
        )

    async def _try_reserve(self, operation: str) -> float:
        async with self._session_factory() as session:
            try:
                async with named_lock(session, GLOBAL_GATE_LOCK, timeout_seconds=5):
                    used = await session.scalar(
                        text(
                            "SELECT COUNT(*) FROM portal_call_logs "
                            "WHERE created_at >= UTC_TIMESTAMP() - INTERVAL :window SECOND"
                        ),
                        {"window": self._policy.global_window_seconds},
                    )
                    if int(used or 0) >= self._policy.global_max_calls_per_window:
                        await session.rollback()
                        return float(self._policy.global_window_seconds) / 4.0
                    await session.execute(
                        text(
                            "INSERT INTO portal_call_logs "
                            "(id, created_at, operation, outcome) "
                            "VALUES (UNHEX(REPLACE(UUID(), '-', '')), UTC_TIMESTAMP(), "
                            ":operation, 'RESERVED')"
                        ),
                        {"operation": operation[:48]},
                    )
                    await session.commit()
            except Exception:
                await session.rollback()
                raise
        return 0.0
