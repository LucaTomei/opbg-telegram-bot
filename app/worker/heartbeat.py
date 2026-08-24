"""Worker heartbeat publication and readiness evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.repositories.base import utc_now
from app.db.repositories.system import HeartbeatRepository
from app.version import APP_VERSION


@dataclass(slots=True)
class HeartbeatPublisher:
    """Publishes the liveness record of one worker process."""

    settings: Settings
    worker_id: str
    started_at: datetime

    async def beat(
        self, session: AsyncSession, *, metadata: dict[str, object] | None = None
    ) -> None:
        await HeartbeatRepository(session).beat(
            worker_id=self.worker_id,
            started_at=self.started_at,
            metadata={"version": APP_VERSION, **(metadata or {})},
        )


async def worker_is_healthy(session: AsyncSession, settings: Settings) -> bool:
    """A deployment is worker-ready when at least one heartbeat is recent."""
    return await HeartbeatRepository(session).has_recent(
        max_age_seconds=settings.worker_heartbeat_stale_seconds
    )


def uptime_seconds(started_at: datetime) -> float:
    return max((utc_now() - started_at).total_seconds(), 0.0)
