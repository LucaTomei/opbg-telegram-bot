"""Worker scheduler: loops, heartbeat, lease release and graceful shutdown."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.composition import Container
from app.config import Settings
from app.db.models.users import TelegramUser
from app.db.models.watches import BookingWatch
from app.db.repositories.base import utc_now
from app.db.repositories.system import HeartbeatRepository
from app.db.repositories.watches import WatchRepository
from app.db.session import Database
from app.worker.heartbeat import HeartbeatPublisher, uptime_seconds, worker_is_healthy
from app.worker.leases import LeaseWindow, build_window, default_worker_id
from app.worker.scheduler import WorkerScheduler
from app.worker.tasks import (
    AppointmentSyncTask,
    CleanupTask,
    ReconciliationTask,
    WatchRunOutcome,
)
from tests.support.factories import make_account, make_patient, make_watch
from tests.support.portal import RecordingSender


@dataclass
class SpyProcessor:
    """Records the monitors the scheduler hands over, without touching the portal."""

    seen: list[str] = field(default_factory=list)
    started: asyncio.Event = field(default_factory=asyncio.Event)

    async def run(
        self, session: AsyncSession, watch: BookingWatch, *, worker_id: str
    ) -> WatchRunOutcome:
        self.seen.append(watch.id.hex)
        self.started.set()
        await WatchRepository(session).release_lease(
            watch, next_check_at=utc_now() + timedelta(hours=1)
        )
        return WatchRunOutcome(outcome="OK", slots_found=0, new_slots=0, booked=False)


def build_scheduler(
    settings: Settings,
    database: Database,
    container: Container,
    processor: SpyProcessor,
    *,
    worker_id: str = "scheduler-test",
) -> WorkerScheduler:
    return WorkerScheduler(
        settings=settings,
        database=database,
        processor=processor,  # type: ignore[arg-type]
        appointment_sync=AppointmentSyncTask(settings, container.appointments),
        reconciliation=ReconciliationTask(settings, container.search),
        cleanup=CleanupTask(settings),
        notifications=container.notifications,
        sender=None,
        renderer=None,
        worker_id=worker_id,
    )


# --- lease helpers -----------------------------------------------------------
def test_the_worker_identifier_is_stable_and_short() -> None:
    identifier = default_worker_id()
    assert identifier == default_worker_id()
    assert 0 < len(identifier) <= 64


def test_a_lease_window_knows_when_to_renew() -> None:
    window = build_window(lease_seconds=120)
    assert isinstance(window, LeaseWindow)
    assert window.remaining_seconds > 0
    assert window.should_renew() is False

    expired = LeaseWindow(
        acquired_at=utc_now() - timedelta(seconds=120),
        expires_at=utc_now() - timedelta(seconds=1),
    )
    assert expired.remaining_seconds == 0
    assert expired.should_renew() is True


def test_a_degenerate_window_always_renews() -> None:
    moment = utc_now()
    assert LeaseWindow(acquired_at=moment, expires_at=moment).should_renew() is True


# --- heartbeat ---------------------------------------------------------------
async def test_the_heartbeat_is_published_and_read_back(
    clean_session: AsyncSession, settings: Settings
) -> None:
    started = utc_now()
    publisher = HeartbeatPublisher(
        settings=settings, worker_id="worker-heartbeat", started_at=started
    )
    await publisher.beat(clean_session, metadata={"loops": 6})
    await publisher.beat(clean_session)

    workers = await HeartbeatRepository(clean_session).list_workers()
    assert [worker.worker_id for worker in workers] == ["worker-heartbeat"]
    assert await worker_is_healthy(clean_session, settings) is True
    assert uptime_seconds(started) >= 0


async def test_a_stale_heartbeat_is_not_healthy(
    clean_session: AsyncSession, settings: Settings
) -> None:
    publisher = HeartbeatPublisher(
        settings=settings, worker_id="worker-stale", started_at=utc_now()
    )
    await publisher.beat(clean_session)
    workers = await HeartbeatRepository(clean_session).list_workers()
    workers[0].last_seen_at = utc_now() - timedelta(days=1)
    await HeartbeatRepository(clean_session).flush()
    assert await worker_is_healthy(clean_session, settings) is False


# --- scheduler ---------------------------------------------------------------
async def test_the_scheduler_processes_a_due_monitor_and_stops_cleanly(
    clean_session: AsyncSession,
    container: Container,
    database: Database,
    user: TelegramUser,
) -> None:
    account = await make_account(clean_session, user)
    patient = await make_patient(clean_session, account)
    watch = await make_watch(
        clean_session,
        user,
        account,
        patient=patient,
        next_check_at=utc_now() - timedelta(minutes=1),
    )
    await clean_session.commit()

    processor = SpyProcessor()
    scheduler = build_scheduler(container.settings, database, container, processor)
    runner = asyncio.create_task(scheduler.run())
    await asyncio.wait_for(processor.started.wait(), timeout=20)
    scheduler.request_stop()
    await asyncio.wait_for(runner, timeout=20)

    assert processor.seen == [watch.id.hex]
    assert scheduler.is_stopping is True


async def test_shutdown_releases_the_leases_of_this_worker(
    clean_session: AsyncSession,
    container: Container,
    database: Database,
    user: TelegramUser,
) -> None:
    account = await make_account(clean_session, user)
    patient = await make_patient(clean_session, account)
    watch = await make_watch(clean_session, user, account, patient=patient)
    watch.lease_owner = "scheduler-shutdown"
    watch.lease_expires_at = utc_now() + timedelta(minutes=5)
    await clean_session.commit()

    scheduler = build_scheduler(
        container.settings,
        database,
        container,
        SpyProcessor(),
        worker_id="scheduler-shutdown",
    )
    await scheduler.shutdown()

    async with database.read_session() as session:
        reloaded = await WatchRepository(session).get_any(watch.id)
        assert reloaded is not None
        assert reloaded.lease_owner is None
        assert reloaded.lease_expires_at is None


async def test_a_failing_loop_does_not_stop_the_others(
    container: Container, database: Database
) -> None:
    """One broken iteration must never take the whole worker down."""
    scheduler = build_scheduler(
        container.settings, database, container, SpyProcessor(), worker_id="scheduler-error"
    )
    calls = {"count": 0}

    async def failing() -> None:
        calls["count"] += 1
        raise RuntimeError("synthetic loop failure")

    from app.worker.scheduler import LoopSpec

    spec = LoopSpec(name="failing", interval_seconds=0.01, handler=failing)
    loop = asyncio.create_task(scheduler._loop(spec))
    await asyncio.sleep(0.1)
    scheduler.request_stop()
    await asyncio.wait_for(loop, timeout=5)
    assert calls["count"] >= 2


async def test_the_notification_loop_is_skipped_without_a_sender(
    container: Container, database: Database
) -> None:
    scheduler = build_scheduler(
        container.settings, database, container, SpyProcessor(), worker_id="scheduler-nosend"
    )
    await scheduler._run_notifications()


async def test_the_notification_loop_delivers_when_configured(
    clean_session: AsyncSession,
    container: Container,
    database: Database,
    user: TelegramUser,
) -> None:
    from app.bot.notifications import render_for_queue
    from app.domain.enums import NotificationEventType

    await container.notifications.queue(
        clean_session,
        user=user,
        event_type=NotificationEventType.REAUTH_REQUIRED,
        subject="scheduler",
        payload={"account_id": "x", "watch_name": "m", "error_code": "AUTH"},
    )
    await clean_session.commit()

    sender = RecordingSender()
    scheduler = build_scheduler(
        container.settings, database, container, SpyProcessor(), worker_id="scheduler-send"
    )
    scheduler.sender = sender
    scheduler.renderer = render_for_queue
    await scheduler._run_notifications()
    assert sender.sent


async def test_the_cleanup_and_reconcile_loops_run(
    clean_session: AsyncSession, container: Container, database: Database
) -> None:
    await clean_session.commit()
    scheduler = build_scheduler(
        container.settings, database, container, SpyProcessor(), worker_id="scheduler-tasks"
    )
    await scheduler._run_cleanup()
    await scheduler._run_reconcile()
    await scheduler._run_appointment_sync()


async def test_the_heartbeat_loop_publishes(
    clean_session: AsyncSession, container: Container, database: Database
) -> None:
    await clean_session.commit()
    scheduler = build_scheduler(
        container.settings, database, container, SpyProcessor(), worker_id="scheduler-beat"
    )
    publisher = HeartbeatPublisher(
        settings=container.settings, worker_id="scheduler-beat", started_at=utc_now()
    )
    await scheduler._run_heartbeat(publisher)

    async with database.read_session() as session:
        workers = await HeartbeatRepository(session).list_workers()
        assert any(worker.worker_id == "scheduler-beat" for worker in workers)
