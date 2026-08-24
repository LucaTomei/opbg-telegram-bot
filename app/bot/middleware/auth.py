"""Authorization middleware.

Every message and every callback is authorised before a handler runs: the user
must exist, be active and — outside the onboarding flow — have accepted the
current consent version.  Administrative routers additionally check the role.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from app.bot import texts
from app.bot.keyboards.reply import main_keyboard
from app.db.repositories.users import UserRepository
from app.domain.enums import UserRole, UserStatus
from app.logging import get_logger

logger = get_logger(__name__)

#: Entry points that must work before the user is registered or has consented.
PUBLIC_COMMANDS: frozenset[str] = frozenset({"/start", "/help", "/privacy"})
CONSENT_CALLBACK_PREFIX = "cs|"


def _telegram_user(event: TelegramObject) -> User | None:
    if isinstance(event, Message):
        return event.from_user
    if isinstance(event, CallbackQuery):
        return event.from_user
    return None


def _is_public(event: TelegramObject) -> bool:
    if isinstance(event, Message):
        text = (event.text or "").strip()
        return any(text.split(" ")[0] == command for command in PUBLIC_COMMANDS)
    if isinstance(event, CallbackQuery):
        return (event.data or "").startswith(CONSENT_CALLBACK_PREFIX)
    return False


class AuthorizationMiddleware(BaseMiddleware):
    """Resolve the internal user and reject unauthorised interactions."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        telegram_user = _telegram_user(event)
        if telegram_user is None or telegram_user.is_bot:
            return None
        session = data["session"]
        users = UserRepository(session)
        user = await users.get_by_telegram_id(telegram_user.id)
        data["telegram_user"] = telegram_user
        data["user"] = user
        data["is_admin"] = user is not None and user.role is UserRole.ADMIN

        if _is_public(event):
            return await handler(event, data)

        if user is None or user.status is UserStatus.DELETED:
            await _reply(event, texts.NOT_AUTHORIZED)
            return None
        if user.status is UserStatus.BLOCKED:
            await _reply(event, texts.USER_BLOCKED)
            return None
        if user.status is UserStatus.SUSPENDED:
            await _reply(event, texts.USER_SUSPENDED)
            return None
        if user.status is UserStatus.PENDING_CONSENT or not user.has_consented:
            await _reply(event, texts.CONSENT_REQUIRED)
            return None

        user.username = telegram_user.username
        user.first_name = telegram_user.first_name
        user.last_name = telegram_user.last_name
        await users.touch(user)
        return await handler(event, data)


class AdminOnlyMiddleware(BaseMiddleware):
    """Guard applied to the administrative router."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("user")
        if user is None or user.role is not UserRole.ADMIN:
            await _reply(event, texts.ADMIN_ONLY)
            return None
        return await handler(event, data)


async def _reply(event: TelegramObject, text: str) -> None:
    if isinstance(event, CallbackQuery):
        await event.answer(text[:200], show_alert=True)
        return
    if isinstance(event, Message):
        await event.answer(text, reply_markup=main_keyboard(is_admin=False))
