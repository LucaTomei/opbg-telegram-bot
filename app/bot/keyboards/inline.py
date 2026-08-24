"""Inline keyboard builders shared by every screen."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot import callbacks, texts

MAX_BUTTON_TEXT = 64


@dataclass(frozen=True, slots=True)
class Entry:
    """One selectable row in a paginated list."""

    label: str
    callback_data: str


def truncate(label: str, limit: int = MAX_BUTTON_TEXT) -> str:
    cleaned = " ".join(label.split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def navigation_row(
    *,
    back_target: str | None = None,
    show_menu: bool = True,
    show_cancel: bool = False,
) -> list[InlineKeyboardButton]:
    """Standard `Indietro` / `Menu` / `Annulla` row present on every screen."""
    row: list[InlineKeyboardButton] = []
    if back_target:
        # The back button carries the target payload directly: every target is
        # already a handled callback, so no extra indirection is needed.
        row.append(InlineKeyboardButton(text=texts.BTN_BACK, callback_data=back_target))
    if show_menu:
        row.append(InlineKeyboardButton(text=texts.BTN_MENU, callback_data=callbacks.menu()))
    if show_cancel:
        row.append(InlineKeyboardButton(text=texts.BTN_CANCEL, callback_data=callbacks.cancel()))
    return row


def pagination_row(
    *,
    namespace: str,
    action: str,
    page: int,
    pages: int,
    extra: Sequence[str | int | uuid.UUID] = (),
) -> list[InlineKeyboardButton]:
    """Build a pager; the indicator itself is a no-op button."""
    if pages <= 1:
        return []
    buttons: list[InlineKeyboardButton] = []
    if page > 0:
        buttons.append(
            InlineKeyboardButton(
                text=texts.BTN_PREV_PAGE,
                callback_data=callbacks.build(namespace, action, page - 1, *extra),
            )
        )
    buttons.append(
        InlineKeyboardButton(
            text=texts.PAGE_INDICATOR.format(page=page + 1, pages=pages),
            callback_data=callbacks.noop(),
        )
    )
    if page + 1 < pages:
        buttons.append(
            InlineKeyboardButton(
                text=texts.BTN_NEXT_PAGE,
                callback_data=callbacks.build(namespace, action, page + 1, *extra),
            )
        )
    return buttons


def build_list(
    entries: Sequence[Entry],
    *,
    namespace: str,
    page_action: str,
    page: int,
    pages: int,
    extra: Sequence[str | int | uuid.UUID] = (),
    back_target: str | None = None,
    show_cancel: bool = False,
    columns: int = 1,
    header_rows: Sequence[Sequence[InlineKeyboardButton]] = (),
    footer_rows: Sequence[Sequence[InlineKeyboardButton]] = (),
) -> InlineKeyboardMarkup:
    """Build a paginated list keyboard with the standard navigation row."""
    builder = InlineKeyboardBuilder()
    for row in header_rows:
        builder.row(*row)
    buttons = [
        InlineKeyboardButton(text=truncate(entry.label), callback_data=entry.callback_data)
        for entry in entries
    ]
    for index in range(0, len(buttons), columns):
        builder.row(*buttons[index : index + columns])
    for row in footer_rows:
        builder.row(*row)
    pager = pagination_row(
        namespace=namespace, action=page_action, page=page, pages=pages, extra=extra
    )
    if pager:
        builder.row(*pager)
    builder.row(*navigation_row(back_target=back_target, show_cancel=show_cancel))
    return builder.as_markup()


def build_actions(
    rows: Sequence[Sequence[Entry]],
    *,
    back_target: str | None = None,
    show_cancel: bool = False,
    show_menu: bool = True,
) -> InlineKeyboardMarkup:
    """Build a simple action keyboard with the standard navigation row."""
    builder = InlineKeyboardBuilder()
    for row in rows:
        builder.row(
            *[
                InlineKeyboardButton(text=truncate(entry.label), callback_data=entry.callback_data)
                for entry in row
            ]
        )
    navigation = navigation_row(
        back_target=back_target, show_menu=show_menu, show_cancel=show_cancel
    )
    if navigation:
        builder.row(*navigation)
    return builder.as_markup()


def url_button(label: str, url: str, *, back_target: str | None = None) -> InlineKeyboardMarkup:
    """A single link button, used for the one-time HTTPS connection page."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=truncate(label), url=url))
    builder.row(*navigation_row(back_target=back_target))
    return builder.as_markup()


def confirmation(
    *, confirm_label: str, confirm_data: str, back_target: str | None = None
) -> InlineKeyboardMarkup:
    """Keyboard for one step of a double confirmation."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=confirm_label, callback_data=confirm_data))
    builder.row(*navigation_row(back_target=back_target, show_cancel=True))
    return builder.as_markup()
