"""Lease helpers shared by the worker loops."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.db.repositories.base import utc_now


def default_worker_id() -> str:
    """Stable-per-process worker identifier used for leases and heartbeats."""
    return f"{socket.gethostname()}-{os.getpid()}"[:64]


@dataclass(frozen=True, slots=True)
class LeaseWindow:
    """Time window during which a leased job belongs to one worker."""

    acquired_at: datetime
    expires_at: datetime

    @property
    def remaining_seconds(self) -> float:
        return max((self.expires_at - utc_now()).total_seconds(), 0.0)

    def should_renew(self, *, safety_ratio: float = 0.5) -> bool:
        total = (self.expires_at - self.acquired_at).total_seconds()
        if total <= 0:
            return True
        return self.remaining_seconds < total * safety_ratio


def build_window(*, lease_seconds: int, now: datetime | None = None) -> LeaseWindow:
    acquired = now or utc_now()
    return LeaseWindow(acquired_at=acquired, expires_at=acquired + timedelta(seconds=lease_seconds))
