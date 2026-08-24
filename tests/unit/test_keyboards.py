"""Keyboard construction unit tests."""

from __future__ import annotations

import uuid

from aiogram.types import InlineKeyboardButton

from app.bot import callbacks, texts
from app.bot.keyboards.inline import (
    Entry,
    build_actions,
    build_list,
    confirmation,
    navigation_row,
    pagination_row,
    truncate,
    url_button,
)
from app.bot.keyboards.reply import MAIN_BUTTON_TEXTS, main_keyboard


def flatten(markup: object) -> list[InlineKeyboardButton]:
    return [button for row in markup.inline_keyboard for button in row]  # type: ignore[attr-defined]


def test_main_keyboard_is_persistent_and_complete() -> None:
    keyboard = main_keyboard(is_admin=False)
    labels = {button.text for row in keyboard.keyboard for button in row}
    assert keyboard.is_persistent is True
    assert keyboard.resize_keyboard is True
    assert labels == MAIN_BUTTON_TEXTS - {texts.BTN_ADMIN}


def test_admin_entry_is_added_only_for_administrators() -> None:
    labels = {
        button.text for row in main_keyboard(is_admin=True).keyboard for button in row
    }
    assert texts.BTN_ADMIN in labels


def test_navigation_row_always_offers_a_way_out() -> None:
    row = navigation_row(back_target="ac|list|0", show_cancel=True)
    assert [button.text for button in row] == [
        texts.BTN_BACK,
        texts.BTN_MENU,
        texts.BTN_CANCEL,
    ]
    assert row[0].callback_data == "ac|list|0"
    assert row[1].callback_data == callbacks.menu()


def test_navigation_row_without_a_back_target() -> None:
    row = navigation_row()
    assert [button.text for button in row] == [texts.BTN_MENU]


def test_pagination_is_hidden_for_a_single_page() -> None:
    assert pagination_row(namespace="ac", action="list", page=0, pages=1) == []


def test_pagination_shows_both_directions_in_the_middle() -> None:
    row = pagination_row(namespace="ac", action="list", page=1, pages=3)
    assert [button.text for button in row] == [
        texts.BTN_PREV_PAGE,
        texts.PAGE_INDICATOR.format(page=2, pages=3),
        texts.BTN_NEXT_PAGE,
    ]
    assert row[0].callback_data == "ac|list|0"
    assert row[2].callback_data == "ac|list|2"


def test_pagination_indicator_is_inert() -> None:
    row = pagination_row(namespace="ac", action="list", page=0, pages=2)
    indicator = next(button for button in row if "/" in (button.text or ""))
    assert indicator.callback_data == callbacks.noop()


def test_list_keyboard_contains_entries_pager_and_navigation() -> None:
    entries = [Entry(f"voce {index}", f"ac|open|{index}") for index in range(3)]
    markup = build_list(
        entries,
        namespace="ac",
        page_action="list",
        page=0,
        pages=2,
        back_target="nv|menu",
    )
    buttons = flatten(markup)
    assert {button.text for button in buttons} >= {"voce 0", "voce 1", "voce 2"}
    assert any(button.text == texts.BTN_BACK for button in buttons)
    assert any(button.text == texts.BTN_MENU for button in buttons)


def test_list_keyboard_supports_headers_and_footers() -> None:
    markup = build_list(
        [],
        namespace="ac",
        page_action="list",
        page=0,
        pages=1,
        header_rows=[[InlineKeyboardButton(text="header", callback_data="nv|noop")]],
        footer_rows=[[InlineKeyboardButton(text="footer", callback_data="nv|noop")]],
    )
    labels = [button.text for button in flatten(markup)]
    assert labels[0] == "header"
    assert "footer" in labels


def test_long_labels_are_truncated_for_telegram() -> None:
    label = truncate("x" * 200)
    assert len(label) <= 64
    assert label.endswith("…")


def test_url_button_carries_no_callback_data() -> None:
    markup = url_button("Apri", "https://example.test/connect/abc")
    button = flatten(markup)[0]
    assert button.url == "https://example.test/connect/abc"
    assert button.callback_data is None


def test_confirmation_keyboard_offers_cancel() -> None:
    markup = confirmation(
        confirm_label="Conferma", confirm_data="wt|delok|x", back_target="wt|open|x"
    )
    labels = [button.text for button in flatten(markup)]
    assert labels[0] == "Conferma"
    assert texts.BTN_CANCEL in labels


def test_action_keyboard_can_hide_the_menu_button() -> None:
    markup = build_actions([[Entry("solo", "nv|noop")]], show_menu=False)
    assert [button.text for button in flatten(markup)] == ["solo"]


def test_every_callback_button_fits_the_telegram_limit() -> None:
    identifier = uuid.uuid4()
    markup = build_list(
        [Entry("voce", callbacks.build("wt", "delok", identifier, "n" * 22))],
        namespace="wt",
        page_action="list",
        page=0,
        pages=1,
    )
    for button in flatten(markup):
        if button.callback_data:
            assert len(button.callback_data.encode("utf-8")) <= 64
