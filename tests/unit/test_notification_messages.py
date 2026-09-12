"""Rendering of the queued notification messages."""

from __future__ import annotations

from typing import Any

from app.bot.notifications import render_notification
from app.db.models.notifications import NotificationEvent
from app.db.models.users import TelegramUser
from app.domain.enums import (
    NotificationEventType,
    PortalAccountStatus,
    UserRole,
    UserStatus,
)


def build_event(event_type: NotificationEventType, payload: dict[str, Any]) -> NotificationEvent:
    """A queued event held in memory, encrypted payload included."""
    event = NotificationEvent(
        telegram_user_id=TelegramUser(telegram_id=1).id,
        event_type=event_type,
        dedup_key=bytes(32),
    )
    event.payload = payload
    return event


def build_user() -> TelegramUser:
    return TelegramUser(telegram_id=99, role=UserRole.USER, status=UserStatus.ACTIVE)


def test_a_lost_login_names_the_account_it_belongs_to() -> None:
    event = build_event(
        NotificationEventType.REAUTH_REQUIRED,
        {
            "account_id": "a" * 32,
            "account_label": "Account principale",
            "status": PortalAccountStatus.REAUTH_REQUIRED.value,
            "error_code": "AUTHENTICATION_FAILED",
        },
    )
    text, keyboard = render_notification(event, build_user())
    assert "Account principale" in text
    assert "non è più valido" in text
    assert "bloccato" not in text
    assert keyboard.inline_keyboard


def test_a_locked_account_says_so_instead_of_asking_for_a_refresh() -> None:
    event = build_event(
        NotificationEventType.REAUTH_REQUIRED,
        {
            "account_id": "b" * 32,
            "account_label": "Secondo",
            "status": PortalAccountStatus.LOCKED.value,
            "error_code": "AUTHENTICATION_FAILED",
        },
    )
    text, _ = render_notification(event, build_user())
    assert "Secondo" in text
    assert "bloccato" in text


def test_an_account_label_cannot_inject_markup() -> None:
    event = build_event(
        NotificationEventType.REAUTH_REQUIRED,
        {"account_label": "<b>grassetto</b>", "status": ""},
    )
    text, _ = render_notification(event, build_user())
    assert "<b>grassetto</b>" not in text
    assert "&lt;b&gt;grassetto&lt;/b&gt;" in text


def test_a_payload_queued_before_the_account_details_still_renders() -> None:
    """Rows already in the queue carry neither label nor status."""
    event = build_event(
        NotificationEventType.REAUTH_REQUIRED,
        {"account_id": "c" * 32, "watch_name": "Monitor", "error_code": "CONNECT_LOCKED"},
    )
    text, _ = render_notification(event, build_user())
    assert "Accesso al portale perso" in text
    assert " · " not in text.splitlines()[0]


def test_a_recovered_account_says_no_action_is_needed() -> None:
    event = build_event(
        NotificationEventType.ACCOUNT_RECOVERED,
        {"account_id": "d" * 32, "account_label": "Account principale", "resumed": 2},
    )
    text, _ = render_notification(event, build_user())
    assert "Account principale" in text
    assert "non devi fare nulla" in text
    assert "i 2 monitoraggi" in text


def test_a_recovery_with_nothing_paused_does_not_invent_monitors() -> None:
    event = build_event(
        NotificationEventType.ACCOUNT_RECOVERED,
        {"account_id": "e" * 32, "account_label": "Secondo", "resumed": 0},
    )
    text, _ = render_notification(event, build_user())
    assert "Nessun monitoraggio era in pausa." in text


def test_a_single_resumed_monitor_reads_as_one() -> None:
    event = build_event(
        NotificationEventType.ACCOUNT_RECOVERED,
        {"account_id": "f" * 32, "account_label": "Terzo", "resumed": 1},
    )
    text, _ = render_notification(event, build_user())
    assert "il monitoraggio che era in pausa" in text
