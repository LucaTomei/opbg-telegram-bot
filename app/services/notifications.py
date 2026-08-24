"""Notification queueing, suppression rules and Telegram delivery."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models.notifications import NotificationEvent
from app.db.models.users import TelegramUser
from app.db.repositories.base import utc_now
from app.db.repositories.notifications import NotificationRepository
from app.db.repositories.users import UserRepository
from app.domain.enums import NotificationEventType, NotificationStatus, UserStatus
from app.domain.filters import ROME, in_quiet_hours, to_local, to_utc
from app.domain.fingerprint import notification_dedup_key
from app.logging import get_logger

logger = get_logger(__name__)

MAX_DELIVERY_ATTEMPTS = 5
RETRY_BACKOFF_SECONDS = (30, 120, 600, 1800)


class TelegramSender(Protocol):
    """Minimal transport contract so the queue can be tested without aiogram."""

    async def send(self, *, chat_id: int, text: str, keyboard: Any | None = None) -> int | None: ...


@dataclass(frozen=True, slots=True)
class QueuedNotification:
    event: NotificationEvent | None
    suppressed: bool


class NotificationService:
    """Queues notifications and applies quiet hours and deduplication."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _should_suppress(
        self,
        user: TelegramUser,
        event_type: NotificationEventType,
        *,
        now: datetime,
        watch_notifications_enabled: bool | None,
    ) -> str | None:
        """Return a suppression reason, or ``None`` when the event may be sent.

        Booking outcomes and re-authentication requests always go through: they
        are the events a user must never miss.
        """
        if event_type.is_critical:
            return None
        if user.status is not UserStatus.ACTIVE:
            return "USER_NOT_ACTIVE"
        if not user.notifications_enabled:
            return "USER_NOTIFICATIONS_DISABLED"
        if watch_notifications_enabled is False:
            return "WATCH_NOTIFICATIONS_DISABLED"
        local = to_local(now, ROME).time()
        if in_quiet_hours(local, user.quiet_hours_start, user.quiet_hours_end):
            return "QUIET_HOURS"
        return None

    def _next_quiet_hours_end(self, user: TelegramUser, now: datetime) -> datetime | None:
        """Compute when a deferred informational notification may be delivered."""
        if user.quiet_hours_end is None:
            return None
        local = to_local(now, ROME)
        candidate = local.replace(
            hour=user.quiet_hours_end.hour,
            minute=user.quiet_hours_end.minute,
            second=0,
            microsecond=0,
        )
        if candidate <= local:
            candidate = candidate + timedelta(days=1)
        return to_utc(candidate).replace(tzinfo=None)

    async def queue(
        self,
        session: AsyncSession,
        *,
        user: TelegramUser,
        event_type: NotificationEventType,
        subject: str,
        payload: dict[str, Any],
        watch_id: uuid.UUID | None = None,
        appointment_id: uuid.UUID | None = None,
        portal_account_id: uuid.UUID | None = None,
        booking_attempt_id: uuid.UUID | None = None,
        watch_notifications_enabled: bool | None = None,
        correlation_id: str | None = None,
        now: datetime | None = None,
    ) -> QueuedNotification:
        """Queue a notification unless it is a duplicate or must be suppressed."""
        moment = now or utc_now()
        repository = NotificationRepository(session)
        dedup = notification_dedup_key(
            event_type=event_type.value, user_id=user.id, subject=subject
        )
        reason = self._should_suppress(
            user,
            event_type,
            now=moment,
            watch_notifications_enabled=watch_notifications_enabled,
        )
        status = NotificationStatus.PENDING
        next_attempt = moment
        if reason == "QUIET_HOURS":
            deferred = self._next_quiet_hours_end(user, moment)
            if deferred is not None:
                next_attempt = deferred
                reason = None
        if reason is not None:
            status = NotificationStatus.SUPPRESSED

        event = await repository.enqueue(
            user_id=user.id,
            event_type=event_type,
            dedup_key=dedup,
            payload=payload,
            watch_id=watch_id,
            appointment_id=appointment_id,
            portal_account_id=portal_account_id,
            booking_attempt_id=booking_attempt_id,
            correlation_id=correlation_id,
            status=status,
            suppressed_reason=reason,
            next_attempt_at=None if status is NotificationStatus.SUPPRESSED else next_attempt,
        )
        return QueuedNotification(event=event, suppressed=reason is not None)

    async def dispatch_pending(
        self,
        session: AsyncSession,
        sender: TelegramSender,
        renderer: Callable[[NotificationEvent, TelegramUser], Awaitable[tuple[str, Any]]],
        *,
        limit: int = 20,
        now: datetime | None = None,
    ) -> int:
        """Send the notifications that are due, applying retry with backoff."""
        moment = now or utc_now()
        repository = NotificationRepository(session)
        users = UserRepository(session)
        due = await repository.claim_due(now=moment, limit=limit)
        sent = 0
        for event in due:
            user = await users.get(event.telegram_user_id)
            if user is None or user.status in (UserStatus.DELETED, UserStatus.BLOCKED):
                await repository.mark_suppressed(event, reason="USER_UNAVAILABLE")
                continue
            reason = self._should_suppress(
                user,
                event.event_type,
                now=moment,
                watch_notifications_enabled=None,
            )
            if reason == "QUIET_HOURS":
                deferred = self._next_quiet_hours_end(user, moment)
                if deferred is not None:
                    await repository.reschedule(event, when=deferred)
                    continue
            if reason is not None:
                await repository.mark_suppressed(event, reason=reason)
                continue
            try:
                text, keyboard = await renderer(event, user)
                message_id = await sender.send(
                    chat_id=user.telegram_id, text=text, keyboard=keyboard
                )
            except Exception as exc:
                retry_index = min(event.attempt_count, len(RETRY_BACKOFF_SECONDS) - 1)
                await repository.mark_failed(
                    event,
                    error_code=type(exc).__name__[:64],
                    retry_in_seconds=RETRY_BACKOFF_SECONDS[retry_index],
                    max_attempts=MAX_DELIVERY_ATTEMPTS,
                )
                logger.warning(
                    "notification delivery failed",
                    extra={
                        "event_code": "NOTIFICATION_FAILED",
                        "notification_id": event.id.hex,
                        "error_type": type(exc).__name__,
                    },
                )
                continue
            await repository.mark_sent(event, message_id=message_id)
            sent += 1
        return sent
