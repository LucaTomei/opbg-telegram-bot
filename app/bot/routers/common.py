"""Helpers shared by every router."""

from __future__ import annotations

from typing import Any

from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)

from app.bot import texts
from app.bot.keyboards.reply import main_keyboard
from app.db.models.users import TelegramUser

PARSE_MODE = "HTML"


async def acknowledge(event: TelegramObject, text: str | None = None) -> None:
    """Answer a callback immediately so the client stops showing a spinner."""
    if isinstance(event, CallbackQuery):
        await event.answer(text[:200] if text else None)


async def notify_working(event: TelegramObject, text: str = texts.PORTAL_WORKING) -> None:
    """Say that a slow portal call is under way.

    Reading availability walks the whole wizard and retries a portal that fails
    often, which can take a minute. Without this the chat stays silent and the
    user cannot tell waiting from broken. The result replaces this message.
    """
    await render(event, text)


async def render(
    event: TelegramObject,
    text: str,
    keyboard: InlineKeyboardMarkup | None = None,
    *,
    force_new: bool = False,
) -> Message | None:
    """Edit the current message when possible, otherwise send a new one.

    Editing keeps the chat readable instead of flooding it with one message per
    interaction, which is what ``docs/02-telegram-ux.md`` requires.
    """
    if isinstance(event, CallbackQuery):
        message = event.message
        if isinstance(message, Message) and not force_new:
            edited = await message.edit_text(text, reply_markup=keyboard, parse_mode=PARSE_MODE)
            return edited if isinstance(edited, Message) else None
        if isinstance(message, Message):
            return await message.answer(text, reply_markup=keyboard, parse_mode=PARSE_MODE)
        return None
    if isinstance(event, Message):
        return await event.answer(text, reply_markup=keyboard, parse_mode=PARSE_MODE)
    return None


async def send_new(
    event: TelegramObject, text: str, keyboard: InlineKeyboardMarkup | None = None
) -> Message | None:
    """Always send a new message, used after a mutating action."""
    return await render(event, text, keyboard, force_new=True)


async def show_main_menu(event: TelegramObject, user: TelegramUser | None) -> None:
    """Send the main menu together with the persistent reply keyboard."""
    is_admin = user is not None and user.is_admin
    if isinstance(event, CallbackQuery) and isinstance(event.message, Message):
        await event.message.answer(
            texts.MAIN_MENU,
            reply_markup=main_keyboard(is_admin=is_admin),
            parse_mode=PARSE_MODE,
        )
        return
    if isinstance(event, Message):
        await event.answer(
            texts.MAIN_MENU,
            reply_markup=main_keyboard(is_admin=is_admin),
            parse_mode=PARSE_MODE,
        )


def page_size(user: TelegramUser | None, fallback: int = 6) -> int:
    return user.page_size if user is not None else fallback


def slot_limit(user: TelegramUser | None, fallback: int = 40) -> int:
    return user.max_slots_shown if user is not None else fallback


def chat_id_of(event: TelegramObject) -> int | None:
    if isinstance(event, CallbackQuery) and isinstance(event.message, Message):
        return event.message.chat.id
    if isinstance(event, Message):
        return event.chat.id
    return None


def context_value(data: dict[str, Any], key: str) -> Any:
    return data.get(key)
