"""Worker scheduler.

Independent loops run concurrently: monitor execution, appointment
synchronisation, notification delivery, reconciliation, cleanup and heartbeat.
A failure inside one loop never stops the others, and shutdown releases every
lease so another worker can pick the jobs up immediately.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

from app.config import Settings
from app.db.repositories.base import utc_now
from app.db.repositories.watches import WatchRepository
from app.db.session import Database
from app.logging import get_logger
from app.services.notifications import NotificationService, TelegramSender
from app.worker.heartbeat import HeartbeatPublisher
from app.worker.leases import default_worker_id
from app.worker.tasks import (
    AccountRecoveryTask,
    AppointmentSyncTask,
    CleanupTask,
    ReconciliationTask,
    WatchProcessor,
)

logger = get_logger(__name__)

CLEANUP_INTERVAL_SECONDS = 900
RECONCILE_INTERVAL_SECONDS = 120
NOTIFICATION_INTERVAL_SECONDS = 5
# How often refused accounts are looked at. How long one of them actually
# waits between two attempts is ACCOUNT_RECOVERY_DELAY_SECONDS, not this.
ACCOUNT_RECOVERY_INTERVAL_SECONDS = 300


@dataclass(slots=True)
class LoopSpec:
    """A named periodic loop."""

    name: str
    interval_seconds: float
    handler: Callable[[], Awaitable[None]]


@dataclass(slots=True)
class WorkerScheduler:
    """Owns every periodic worker loop and their graceful shutdown."""

    settings: Settings
    database: Database
    processor: WatchProcessor
    appointment_sync: AppointmentSyncTask
    reconciliation: ReconciliationTask
    cleanup: CleanupTask
    notifications: NotificationService
    sender: TelegramSender | None
    renderer: Callable[..., Awaitable[tuple[str, object]]] | None = None
    account_recovery: AccountRecoveryTask | None = None
    worker_id: str = field(default_factory=default_worker_id)
    started_at: datetime = field(default_factory=utc_now)
    _stopping: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    _tasks: list[asyncio.Task[None]] = field(default_factory=list, init=False)

    def request_stop(self) -> None:
        """Ask every loop to finish the current iteration and exit."""
        self._stopping.set()

    @property
    def is_stopping(self) -> bool:
        return self._stopping.is_set()

    async def run(self) -> None:
        """Start every loop and wait until a stop is requested."""
        publisher = HeartbeatPublisher(
            settings=self.settings, worker_id=self.worker_id, started_at=self.started_at
        )
        specs = [
            LoopSpec("watches", self.settings.worker_poll_interval_seconds, self._run_watches),
            LoopSpec(
                "appointments",
                max(self.settings.appointment_sync_interval_seconds / 4, 60),
                self._run_appointment_sync,
            ),
            LoopSpec("reconcile", RECONCILE_INTERVAL_SECONDS, self._run_reconcile),
            LoopSpec("cleanup", CLEANUP_INTERVAL_SECONDS, self._run_cleanup),
            LoopSpec("notifications", NOTIFICATION_INTERVAL_SECONDS, self._run_notifications),
            LoopSpec(
                "account-recovery",
                ACCOUNT_RECOVERY_INTERVAL_SECONDS,
                self._run_account_recovery,
            ),
            LoopSpec(
                "heartbeat",
                self.settings.worker_heartbeat_seconds,
                lambda: self._run_heartbeat(publisher),
            ),
        ]
        self._tasks = [
            asyncio.create_task(self._loop(spec), name=f"worker-{spec.name}") for spec in specs
        ]
        logger.info(
            "worker started",
            extra={"event_code": "WORKER_STARTED", "worker_id": self.worker_id},
        )
        await self._stopping.wait()
        await self.shutdown()

    async def shutdown(self) -> None:
        """Cancel every loop and release the leases owned by this worker."""
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        async with self.database.session() as session:
            released = await WatchRepository(session).release_leases_of_worker(self.worker_id)
        logger.info(
            "worker stopped",
            extra={
                "event_code": "WORKER_STOPPED",
                "worker_id": self.worker_id,
                "released_leases": released,
            },
        )

    async def _loop(self, spec: LoopSpec) -> None:
        while not self._stopping.is_set():
            try:
                await spec.handler()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "worker loop iteration failed",
                    extra={"event_code": "WORKER_LOOP_ERROR", "loop": spec.name},
                )
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=spec.interval_seconds)
            except TimeoutError:
                continue

    async def _run_watches(self) -> None:
        async with self.database.session() as session:
            leased = await WatchRepository(session).claim_due(
                worker_id=self.worker_id,
                now=utc_now(),
                lease_seconds=self.settings.monitor_lease_seconds,
                limit=self.settings.monitor_batch_size,
            )
            watch_ids = [watch.id for watch in leased]
        for watch_id in watch_ids:
            if self._stopping.is_set():
                break
            async with self.database.session() as session:
                watch = await WatchRepository(session).get_any(watch_id)
                if watch is None or watch.lease_owner != self.worker_id[:64]:
                    continue
                await self.processor.run(session, watch, worker_id=self.worker_id)

    async def _run_appointment_sync(self) -> None:
        async with self.database.session() as session:
            await self.appointment_sync.run(session)

    async def _run_reconcile(self) -> None:
        async with self.database.session() as session:
            await self.reconciliation.run(session)

    async def _run_cleanup(self) -> None:
        async with self.database.session() as session:
            removed = await self.cleanup.run(session)
        logger.info(
            "retention cleanup completed",
            extra={"event_code": "CLEANUP_DONE", **removed},
        )

    async def _run_notifications(self) -> None:
        if self.sender is None or self.renderer is None:
            return
        async with self.database.session() as session:
            await self.notifications.dispatch_pending(session, self.sender, self.renderer, limit=20)

    async def _run_account_recovery(self) -> None:
        if self.account_recovery is None:
            return
        async with self.database.session() as session:
            recovered = await self.account_recovery.run(session)
        if recovered:
            logger.info(
                "suspended accounts came back on their own",
                extra={"event_code": "ACCOUNT_RECOVERY_DONE", "recovered": recovered},
            )

    async def _run_heartbeat(self, publisher: HeartbeatPublisher) -> None:
        async with self.database.session() as session:
            await publisher.beat(session, metadata={"loops": len(self._tasks)})
