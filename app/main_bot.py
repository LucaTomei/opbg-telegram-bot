"""Telegram bot entrypoint.

Long polling is used, so no inbound port is required.  Every update passes
through the context, error, authorization and idempotency middlewares before
reaching a router.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from types import FrameType

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.types import BotCommand, BotCommandScopeDefault

from app.bot.middleware.auth import AdminOnlyMiddleware, AuthorizationMiddleware
from app.bot.middleware.context import ContextMiddleware
from app.bot.middleware.errors import ErrorBoundaryMiddleware
from app.bot.middleware.idempotency import CallbackIdempotencyMiddleware
from app.bot.routers import (
    accounts,
    admin,
    appointments,
    booking,
    menu,
    start,
    watches,
)
from app.bot.routers import (
    settings as settings_router,
)
from app.bot.storage import MySQLStorage
from app.composition import Container, build_container
from app.logging import get_logger
from app.version import APP_VERSION

logger = get_logger(__name__)

BOT_COMMANDS: tuple[BotCommand, ...] = (
    BotCommand(command="start", description="Avvia o riprendi l'assistente"),
    BotCommand(command="menu", description="Menu principale"),
    BotCommand(command="cancel", description="Annulla l'operazione in corso"),
    BotCommand(command="status", description="Stato del tuo profilo"),
    BotCommand(command="privacy", description="Informativa privacy"),
    BotCommand(command="help", description="Guida rapida"),
)


def build_dispatcher(container: Container) -> Dispatcher:
    """Assemble the dispatcher, its middlewares and every router."""
    dispatcher = Dispatcher(storage=MySQLStorage(container.database, container.settings))

    context = ContextMiddleware(container)
    errors = ErrorBoundaryMiddleware()
    authorization = AuthorizationMiddleware()
    idempotency = CallbackIdempotencyMiddleware()

    for observer in (dispatcher.message, dispatcher.callback_query):
        # The error boundary wraps the unit of work: a failure must roll the
        # transaction back before the user is answered.
        observer.outer_middleware(errors)
        observer.outer_middleware(context)
        observer.outer_middleware(authorization)
    dispatcher.callback_query.outer_middleware(idempotency)

    admin.router.message.middleware(AdminOnlyMiddleware())
    admin.router.callback_query.middleware(AdminOnlyMiddleware())

    dispatcher.include_router(start.router)
    dispatcher.include_router(menu.router)
    dispatcher.include_router(accounts.router)
    dispatcher.include_router(booking.router)
    dispatcher.include_router(watches.router)
    dispatcher.include_router(appointments.router)
    dispatcher.include_router(settings_router.router)
    dispatcher.include_router(admin.router)
    return dispatcher


def build_bot(container: Container) -> Bot:
    return Bot(
        token=container.settings.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


async def run() -> None:
    """Start long polling until a termination signal is received."""
    container = build_container(service_name="bot")
    bot = build_bot(container)
    dispatcher = build_dispatcher(container)

    async with container.database.session() as session:
        await container.users.bootstrap_admins(session)

    try:
        await bot.set_my_commands(list(BOT_COMMANDS), scope=BotCommandScopeDefault())
    except TelegramUnauthorizedError:
        # A wrong or revoked token is an operator error, not a crash: report it
        # clearly and shut down cleanly instead of leaking an open session.
        logger.error(
            "the Telegram bot token was rejected; check TELEGRAM_BOT_TOKEN",
            extra={"event_code": "BOT_TOKEN_REJECTED"},
        )
        await bot.session.close()
        await container.dispose()
        raise SystemExit(2) from None
    logger.info("bot starting", extra={"event_code": "BOT_STARTUP", "version": APP_VERSION})

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_stop(_signal: int | None = None, _frame: FrameType | None = None) -> None:
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _request_stop)

    polling = asyncio.create_task(
        dispatcher.start_polling(bot, handle_signals=False), name="bot-polling"
    )
    stopper = asyncio.create_task(stop_event.wait(), name="bot-stop")
    await asyncio.wait({polling, stopper}, return_when=asyncio.FIRST_COMPLETED)

    await dispatcher.stop_polling()
    polling.cancel()
    stopper.cancel()
    for task in (polling, stopper):
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await bot.session.close()
    await container.dispose()
    logger.info("bot stopped", extra={"event_code": "BOT_SHUTDOWN"})


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
