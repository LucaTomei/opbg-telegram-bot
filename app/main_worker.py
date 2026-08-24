"""Worker entrypoint.

The worker owns the scheduling loops.  On shutdown it releases every lease it
holds so another worker can pick the monitors up immediately.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from types import FrameType

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.bot.notifications import TelegramNotificationSender, render_for_queue
from app.composition import build_container
from app.logging import get_logger
from app.version import APP_VERSION
from app.worker.leases import default_worker_id
from app.worker.scheduler import WorkerScheduler
from app.worker.tasks import (
    AppointmentSyncTask,
    CleanupTask,
    ReconciliationTask,
    WatchProcessor,
)

logger = get_logger(__name__)


async def run() -> None:
    """Build the scheduler and run until a termination signal is received."""
    container = build_container(service_name="worker")
    bot = Bot(
        token=container.settings.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    scheduler = WorkerScheduler(
        settings=container.settings,
        database=container.database,
        processor=WatchProcessor(
            container.settings, container.search, container.bookings, container.notifications
        ),
        appointment_sync=AppointmentSyncTask(container.settings, container.appointments),
        reconciliation=ReconciliationTask(container.settings, container.search),
        cleanup=CleanupTask(container.settings),
        notifications=container.notifications,
        sender=TelegramNotificationSender(bot),
        renderer=render_for_queue,
        worker_id=container.settings.worker_id or default_worker_id(),
    )

    loop = asyncio.get_running_loop()

    def _request_stop(_signal: int | None = None, _frame: FrameType | None = None) -> None:
        scheduler.request_stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _request_stop)

    logger.info(
        "worker starting",
        extra={"event_code": "WORKER_STARTUP", "version": APP_VERSION},
    )
    try:
        await scheduler.run()
    finally:
        await bot.session.close()
        await container.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
