"""Persistent reply keyboards."""

from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from app.bot import texts

MAIN_BUTTONS: tuple[tuple[str, str], ...] = (
    (texts.BTN_BOOK, texts.BTN_WATCHES),
    (texts.BTN_APPOINTMENTS, texts.BTN_ACCOUNTS),
    (texts.BTN_SETTINGS, texts.BTN_HELP),
)


def main_keyboard(*, is_admin: bool) -> ReplyKeyboardMarkup:
    """Build the persistent main keyboard, adding the admin entry when due."""
    rows = [[KeyboardButton(text=left), KeyboardButton(text=right)] for left, right in MAIN_BUTTONS]
    if is_admin:
        rows.append([KeyboardButton(text=texts.BTN_ADMIN)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Scegli un'operazione",
    )


MAIN_BUTTON_TEXTS: frozenset[str] = frozenset(
    {
        texts.BTN_BOOK,
        texts.BTN_WATCHES,
        texts.BTN_APPOINTMENTS,
        texts.BTN_ACCOUNTS,
        texts.BTN_SETTINGS,
        texts.BTN_HELP,
        texts.BTN_ADMIN,
    }
)
