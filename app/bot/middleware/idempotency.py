"""Idempotency middleware for repeated Telegram updates and button taps."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, TelegramObject, Update

from app.bot import texts
from app.db.repositories.conversations import CallbackConsumptionRepository
from app.domain.fingerprint import callback_dedup_key
from app.logging import get_logger

logger = get_logger(__name__)

DEDUP_TTL_SECONDS = 900

#: Read-only actions may safely be repeated, so they are not deduplicated.
IDEMPOTENT_ACTIONS: frozenset[str] = frozenset(
    {"page", "menu", "back", "noop", "refresh", "open", "list", "detail", "show"}
)


class CallbackIdempotencyMiddleware(BaseMiddleware):
    """Ensure a duplicate callback never triggers a second mutation."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, CallbackQuery):
            return await handler(event, data)
        payload = event.data or ""
        action = payload.split("|")[1] if "|" in payload else ""
        if action in IDEMPOTENT_ACTIONS:
            return await handler(event, data)

        update: Update | None = data.get("event_update")
        update_id = update.update_id if update is not None else 0
        user = data.get("user")
        key = callback_dedup_key(update_id=update_id, callback_id=event.id, action=payload)
        session = data["session"]
        claimed = await CallbackConsumptionRepository(session).claim(
            dedup_key=key,
            user_id=None if user is None else user.id,
            ttl_seconds=DEDUP_TTL_SECONDS,
        )
        if not claimed:
            await event.answer(texts.ALREADY_HANDLED, show_alert=False)
            logger.info(
                "duplicate callback ignored",
                extra={"event_code": "CALLBACK_DUPLICATE", "action": action},
            )
            return None
        return await handler(event, data)
