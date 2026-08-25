"""Error boundary translating domain errors into Italian user messages."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.bot import texts
from app.bot.presenters.errors import describe_error
from app.domain.errors import DomainError
from app.logging import correlation_scope, get_logger

logger = get_logger(__name__)


class ErrorBoundaryMiddleware(BaseMiddleware):
    """Catch expected and unexpected errors and answer the user safely.

    It is registered *outside* the unit of work, so an exception first rolls
    back the transaction of the update and only then reaches the user: a failed
    handler can never leave half of its changes committed.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        with correlation_scope() as correlation_id:
            data["correlation_id"] = correlation_id
            return await self._guard(handler, event, data, correlation_id)

    async def _guard(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
        correlation_id: str,
    ) -> Any:
        try:
            return await handler(event, data)
        except TelegramBadRequest as exc:
            # Editing a message with identical content is not an error.
            if "message is not modified" in str(exc).lower():
                if isinstance(event, CallbackQuery):
                    await event.answer()
                return None
            logger.warning(
                "telegram rejected the request",
                extra={"event_code": "TELEGRAM_BAD_REQUEST"},
            )
            await _notify(event, texts.GENERIC_ERROR.format(cid=correlation_id))
            return None
        except DomainError as exc:
            logger.info(
                "handled domain error",
                extra={"event_code": exc.code, "correlation_id": correlation_id},
            )
            await _notify(event, describe_error(exc, correlation_id))
            return None
        except Exception:
            logger.exception(
                "unhandled error while processing an update",
                extra={"event_code": "BOT_UNHANDLED_ERROR"},
            )
            await _notify(event, texts.GENERIC_ERROR.format(cid=correlation_id))
            return None


async def _notify(event: TelegramObject, text: str) -> None:
    """Deliver the message to the user, whatever else has already happened.

    Answering a callback twice is refused by Telegram, and so is answering one
    that expired while a slow portal call was in flight. Either way the
    acknowledgement is a courtesy and the explanation is the point, so a failed
    answer must never keep the user in the dark.
    """
    if isinstance(event, CallbackQuery):
        try:
            await event.answer()
        except TelegramBadRequest:
            logger.debug(
                "the callback could not be acknowledged again",
                extra={"event_code": "CALLBACK_ALREADY_ANSWERED"},
            )
        if event.message is not None and isinstance(event.message, Message):
            await event.message.answer(text)
        return
    if isinstance(event, Message):
        await event.answer(text)
