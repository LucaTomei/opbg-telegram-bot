"""Quiet hours, suppression and retry rules of the notification queue."""

from __future__ import annotations

from datetime import UTC, datetime, time

import pytest

from app.config import Settings
from app.db.models.users import TelegramUser
from app.domain.enums import NotificationEventType, UserStatus
from app.services.notifications import RETRY_BACKOFF_SECONDS, NotificationService


def make_user(**overrides: object) -> TelegramUser:
    """Build a transient user with the defaults the database would apply."""
    user = TelegramUser(telegram_id=1, status=UserStatus.ACTIVE)
    user.notifications_enabled = True
    user.quiet_hours_start = None
    user.quiet_hours_end = None
    for key, value in overrides.items():
        setattr(user, key, value)
    return user


@pytest.fixture
def service(settings: Settings) -> NotificationService:
    return NotificationService(settings)


def suppression(
    service: NotificationService,
    user: TelegramUser,
    event: NotificationEventType,
    *,
    now: datetime,
    watch_enabled: bool | None = None,
) -> str | None:
    return service._should_suppress(  # noqa: SLF001 - the rule is the unit under test
        user, event, now=now, watch_notifications_enabled=watch_enabled
    )


NIGHT = datetime(2026, 9, 1, 23, 30, tzinfo=UTC)
DAY = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


def test_informational_events_are_suppressed_during_quiet_hours(
    service: NotificationService,
) -> None:
    user = make_user(quiet_hours_start=time(22, 0), quiet_hours_end=time(7, 0))
    assert (
        suppression(service, user, NotificationEventType.NEW_SLOT, now=NIGHT) == "QUIET_HOURS"
    )


@pytest.mark.parametrize(
    "event",
    [
        NotificationEventType.AUTOBOOK_SUCCESS,
        NotificationEventType.AUTOBOOK_FAILED,
        NotificationEventType.AUTOBOOK_AMBIGUOUS,
        NotificationEventType.REAUTH_REQUIRED,
        NotificationEventType.CANCELLATION_SUCCESS,
        NotificationEventType.CANCELLATION_FAILED,
    ],
)
def test_critical_events_bypass_every_suppression_rule(
    service: NotificationService, event: NotificationEventType
) -> None:
    user = make_user(
        notifications_enabled=False,
        quiet_hours_start=time(22, 0),
        quiet_hours_end=time(7, 0),
    )
    assert suppression(service, user, event, now=NIGHT, watch_enabled=False) is None
    assert event.is_critical is True


def test_global_switch_suppresses_informational_events(service: NotificationService) -> None:
    user = make_user(notifications_enabled=False)
    assert (
        suppression(service, user, NotificationEventType.NEW_SLOT, now=DAY)
        == "USER_NOTIFICATIONS_DISABLED"
    )


def test_watch_switch_is_independent_from_the_global_one(
    service: NotificationService,
) -> None:
    user = make_user(notifications_enabled=True)
    assert (
        suppression(
            service, user, NotificationEventType.NEW_SLOT, now=DAY, watch_enabled=False
        )
        == "WATCH_NOTIFICATIONS_DISABLED"
    )
    assert (
        suppression(service, user, NotificationEventType.NEW_SLOT, now=DAY, watch_enabled=True)
        is None
    )


def test_inactive_users_never_receive_informational_events(
    service: NotificationService,
) -> None:
    user = make_user(status=UserStatus.SUSPENDED)
    assert (
        suppression(service, user, NotificationEventType.NEW_SLOT, now=DAY)
        == "USER_NOT_ACTIVE"
    )


def test_quiet_hours_deferral_targets_the_end_of_the_window(
    service: NotificationService,
) -> None:
    user = make_user(quiet_hours_start=time(22, 0), quiet_hours_end=time(7, 0))
    deferred = service._next_quiet_hours_end(user, NIGHT)  # noqa: SLF001
    assert deferred is not None
    # 07:00 Italian time on 2 September is 05:00 UTC.
    assert deferred == datetime(2026, 9, 2, 5, 0)


def test_no_deferral_without_quiet_hours(service: NotificationService) -> None:
    assert service._next_quiet_hours_end(make_user(), NIGHT) is None  # noqa: SLF001


def test_retry_backoff_is_monotonic() -> None:
    assert list(RETRY_BACKOFF_SECONDS) == sorted(RETRY_BACKOFF_SECONDS)
    assert RETRY_BACKOFF_SECONDS[0] >= 30
