"""Cross-process portal gate and circuit breaker behaviour."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.repositories.base import utc_now
from app.db.repositories.system import PortalCallLogRepository
from app.db.session import create_session_factory
from app.domain.enums import BreakerScope, BreakerState
from app.domain.errors import CircuitOpenError
from app.portal.circuit_breaker import BreakerPolicy, CircuitBreakerService
from app.portal.rate_limit import DatabasePortalGate, RateLimitPolicy

ACCOUNT = uuid.UUID("00000000-0000-7000-8000-0000000000ff").hex


class Sleeper:
    """Records the waits requested by the gate instead of really sleeping."""

    def __init__(self) -> None:
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


# --- database portal gate ----------------------------------------------------
async def test_the_gate_records_every_reservation(
    clean_session: AsyncSession, engine: AsyncEngine
) -> None:
    await clean_session.commit()
    factory = create_session_factory(engine)
    gate = DatabasePortalGate(
        factory, RateLimitPolicy(global_window_seconds=60, global_max_calls_per_window=5)
    )
    for _ in range(3):
        await gate.acquire(operation="availability", account_key=ACCOUNT)

    async with factory() as session:
        assert await PortalCallLogRepository(session).recent_count(window_seconds=60) == 3


async def test_the_gate_waits_when_the_window_is_full(
    clean_session: AsyncSession, engine: AsyncEngine
) -> None:
    await clean_session.commit()
    factory = create_session_factory(engine)
    sleeper = Sleeper()
    gate = DatabasePortalGate(
        factory,
        RateLimitPolicy(global_window_seconds=60, global_max_calls_per_window=1),
        sleep=sleeper,
        max_waits=3,
    )
    await gate.acquire(operation="availability", account_key=ACCOUNT)
    await gate.acquire(operation="availability", account_key=ACCOUNT)
    assert sleeper.waits, "the gate did not wait when the budget was exhausted"
    assert all(0 < wait <= 5.0 for wait in sleeper.waits)


async def test_the_gate_gives_up_after_the_maximum_wait(
    clean_session: AsyncSession, engine: AsyncEngine
) -> None:
    """Saturation must never block a worker for ever."""
    await clean_session.commit()
    factory = create_session_factory(engine)
    sleeper = Sleeper()
    gate = DatabasePortalGate(
        factory,
        RateLimitPolicy(global_window_seconds=60, global_max_calls_per_window=0),
        sleep=sleeper,
        max_waits=2,
    )
    await gate.acquire(operation="availability", account_key=ACCOUNT)
    assert len(sleeper.waits) == 2


async def test_old_portal_calls_leave_the_window(
    clean_session: AsyncSession, engine: AsyncEngine
) -> None:
    await clean_session.commit()
    factory = create_session_factory(engine)
    async with factory() as session:
        await PortalCallLogRepository(session).record(operation="availability", outcome="RESERVED")
        await session.execute(
            text("UPDATE portal_call_logs SET created_at = created_at - INTERVAL 1 DAY")
        )
        await session.commit()

    gate = DatabasePortalGate(
        factory, RateLimitPolicy(global_window_seconds=60, global_max_calls_per_window=1)
    )
    await gate.acquire(operation="availability", account_key=ACCOUNT)
    async with factory() as session:
        assert await PortalCallLogRepository(session).recent_count(window_seconds=60) == 1


# --- circuit breaker ---------------------------------------------------------
@pytest.fixture
def breaker() -> CircuitBreakerService:
    return CircuitBreakerService(
        BreakerPolicy(
            account_failure_threshold=2,
            account_cooldown_seconds=60,
            global_failure_threshold=3,
            global_cooldown_seconds=30,
        )
    )


async def test_a_closed_breaker_lets_traffic_through(
    clean_session: AsyncSession, breaker: CircuitBreakerService
) -> None:
    await breaker.ensure_closed(clean_session, scope=BreakerScope.GLOBAL)
    await breaker.ensure_closed(clean_session, scope=BreakerScope.ACCOUNT, subject=ACCOUNT)


async def test_failures_open_the_account_breaker_only(
    clean_session: AsyncSession, breaker: CircuitBreakerService
) -> None:
    for _ in range(2):
        await breaker.record_failure(
            clean_session,
            scope=BreakerScope.ACCOUNT,
            subject=ACCOUNT,
            error_code="PORTAL_UNAVAILABLE",
        )
    with pytest.raises(CircuitOpenError):
        await breaker.ensure_closed(clean_session, scope=BreakerScope.ACCOUNT, subject=ACCOUNT)
    # The global breaker is untouched.
    await breaker.ensure_closed(clean_session, scope=BreakerScope.GLOBAL)


async def test_the_open_breaker_reports_the_remaining_cooldown(
    clean_session: AsyncSession, breaker: CircuitBreakerService
) -> None:
    for _ in range(2):
        await breaker.record_failure(
            clean_session, scope=BreakerScope.ACCOUNT, subject=ACCOUNT, error_code="X"
        )
    with pytest.raises(CircuitOpenError) as failure:
        await breaker.ensure_closed(clean_session, scope=BreakerScope.ACCOUNT, subject=ACCOUNT)
    assert failure.value.retry_after_seconds > 0


async def test_the_breaker_goes_half_open_after_the_cooldown(
    clean_session: AsyncSession, breaker: CircuitBreakerService
) -> None:
    for _ in range(2):
        await breaker.record_failure(
            clean_session, scope=BreakerScope.ACCOUNT, subject=ACCOUNT, error_code="X"
        )
    states = await breaker.list_states(clean_session)
    record = next(item for item in states if item.subject_key == ACCOUNT)
    record.reopen_at = utc_now() - timedelta(seconds=1)
    await clean_session.flush()

    await breaker.ensure_closed(clean_session, scope=BreakerScope.ACCOUNT, subject=ACCOUNT)
    states = await breaker.list_states(clean_session)
    record = next(item for item in states if item.subject_key == ACCOUNT)
    assert record.state is BreakerState.HALF_OPEN
    assert record.half_open_token is not None


async def test_a_failure_in_half_open_reopens_immediately(
    clean_session: AsyncSession, breaker: CircuitBreakerService
) -> None:
    await breaker.force_state(
        clean_session,
        scope=BreakerScope.ACCOUNT,
        subject=ACCOUNT,
        state=BreakerState.HALF_OPEN,
        admin_user_id=None,
        reason="prova",
    )
    opened = await breaker.record_failure(
        clean_session, scope=BreakerScope.ACCOUNT, subject=ACCOUNT, error_code="X"
    )
    assert opened is True


async def test_a_success_closes_the_breaker(
    clean_session: AsyncSession, breaker: CircuitBreakerService
) -> None:
    await breaker.record_failure(clean_session, scope=BreakerScope.GLOBAL, error_code="X")
    await breaker.record_success(clean_session, scope=BreakerScope.GLOBAL)
    states = await breaker.list_states(clean_session)
    record = next(item for item in states if item.scope is BreakerScope.GLOBAL)
    assert record.state is BreakerState.CLOSED
    assert record.failure_count == 0


async def test_an_administrator_can_force_the_state(
    clean_session: AsyncSession, breaker: CircuitBreakerService
) -> None:
    opened = await breaker.force_state(
        clean_session,
        scope=BreakerScope.GLOBAL,
        subject="GLOBAL",
        state=BreakerState.OPEN,
        admin_user_id=None,
        reason="manutenzione portale",
    )
    assert opened.state is BreakerState.OPEN
    assert opened.reopen_at is not None
    with pytest.raises(CircuitOpenError):
        await breaker.ensure_closed(clean_session, scope=BreakerScope.GLOBAL)

    closed = await breaker.force_state(
        clean_session,
        scope=BreakerScope.GLOBAL,
        subject="GLOBAL",
        state=BreakerState.CLOSED,
        admin_user_id=None,
        reason="fine manutenzione",
    )
    assert closed.state is BreakerState.CLOSED
    await breaker.ensure_closed(clean_session, scope=BreakerScope.GLOBAL)


async def test_half_open_allows_a_single_probe_token(
    clean_session: AsyncSession, breaker: CircuitBreakerService
) -> None:
    record = await breaker.force_state(
        clean_session,
        scope=BreakerScope.GLOBAL,
        subject="GLOBAL",
        state=BreakerState.HALF_OPEN,
        admin_user_id=None,
        reason="prova",
    )
    record.half_open_token = None
    await clean_session.flush()
    await breaker.ensure_closed(clean_session, scope=BreakerScope.GLOBAL)
    states = await breaker.list_states(clean_session)
    assert (
        next(item for item in states if item.scope is BreakerScope.GLOBAL).half_open_token
        is not None
    )
