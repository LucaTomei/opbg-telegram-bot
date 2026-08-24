"""In-process rate limiter unit tests."""

from __future__ import annotations

import asyncio

from app.portal.rate_limit import AsyncRateLimiter, NullPortalGate, RateLimitPolicy


class Clock:
    """Deterministic monotonic clock advanced by the fake sleeper."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def build(policy: RateLimitPolicy) -> tuple[AsyncRateLimiter, Clock]:
    clock = Clock()
    limiter = AsyncRateLimiter(policy, jitter=lambda: 0.0, clock=clock, sleep=clock.sleep)
    return limiter, clock


async def test_first_call_is_not_delayed() -> None:
    limiter, clock = build(RateLimitPolicy(min_interval_seconds=1.0))
    async with limiter.slot():
        pass
    assert clock.sleeps == []


async def test_consecutive_calls_respect_the_global_spacing() -> None:
    limiter, clock = build(
        RateLimitPolicy(min_interval_seconds=2.0, per_account_min_interval_seconds=0.0)
    )
    async with limiter.slot():
        pass
    async with limiter.slot():
        pass
    assert clock.sleeps == [2.0]


async def test_per_account_spacing_is_stricter_than_the_global_one() -> None:
    limiter, clock = build(
        RateLimitPolicy(min_interval_seconds=0.5, per_account_min_interval_seconds=3.0)
    )
    async with limiter.slot(account_key="a"):
        pass
    async with limiter.slot(account_key="a"):
        pass
    assert clock.sleeps == [3.0]


async def test_different_accounts_only_pay_the_global_spacing() -> None:
    limiter, clock = build(
        RateLimitPolicy(min_interval_seconds=0.5, per_account_min_interval_seconds=3.0)
    )
    async with limiter.slot(account_key="a"):
        pass
    async with limiter.slot(account_key="b"):
        pass
    assert clock.sleeps == [0.5]


async def test_jitter_is_added_to_the_delay() -> None:
    clock = Clock()
    limiter = AsyncRateLimiter(
        RateLimitPolicy(min_interval_seconds=1.0, per_account_min_interval_seconds=0.0),
        jitter=lambda: 0.25,
        clock=clock,
        sleep=clock.sleep,
    )
    async with limiter.slot():
        pass
    async with limiter.slot():
        pass
    assert clock.sleeps == [0.25, 1.25]


async def test_concurrency_is_capped() -> None:
    limiter = AsyncRateLimiter(
        RateLimitPolicy(
            max_concurrency=2, min_interval_seconds=0.0, per_account_min_interval_seconds=0.0
        ),
        jitter=lambda: 0.0,
    )
    active = 0
    peak = 0
    release = asyncio.Event()

    async def worker() -> None:
        nonlocal active, peak
        async with limiter.slot():
            active += 1
            peak = max(peak, active)
            await release.wait()
            active -= 1

    tasks = [asyncio.create_task(worker()) for _ in range(5)]
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(*tasks)
    assert peak <= 2


async def test_null_gate_never_blocks() -> None:
    gate = NullPortalGate()
    await gate.acquire(operation="test", account_key=None)
