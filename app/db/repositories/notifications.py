"""Repository for the outbound notification queue."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, delete, select

from app.db.models.notifications import NotificationEvent
from app.db.repositories.base import BaseRepository, Page, utc_now
from app.domain.enums import NotificationEventType, NotificationStatus


class NotificationRepository(BaseRepository[NotificationEvent]):
    model = NotificationEvent

    async def enqueue(
        self,
        *,
        user_id: uuid.UUID,
        event_type: NotificationEventType,
        dedup_key: bytes,
        payload: dict[str, Any],
        watch_id: uuid.UUID | None = None,
        appointment_id: uuid.UUID | None = None,
        portal_account_id: uuid.UUID | None = None,
        booking_attempt_id: uuid.UUID | None = None,
        correlation_id: str | None = None,
        status: NotificationStatus = NotificationStatus.PENDING,
        suppressed_reason: str | None = None,
        next_attempt_at: datetime | None = None,
    ) -> NotificationEvent | None:
        """Queue a notification; returns ``None`` when it is a duplicate."""
        event = NotificationEvent(
            telegram_user_id=user_id,
            watch_id=watch_id,
            appointment_id=appointment_id,
            portal_account_id=portal_account_id,
            booking_attempt_id=booking_attempt_id,
            event_type=event_type,
            dedup_key=dedup_key,
            status=status,
            suppressed_reason=None if suppressed_reason is None else suppressed_reason[:64],
            next_attempt_at=next_attempt_at or utc_now(),
            correlation_id=None if correlation_id is None else correlation_id[:32],
        )
        event.payload = payload
        if not await self.try_add(event):
            return None
        return event

    async def claim_due(self, *, now: datetime, limit: int) -> list[NotificationEvent]:
        statement = (
            select(NotificationEvent)
            .where(
                NotificationEvent.status == NotificationStatus.PENDING,
                NotificationEvent.next_attempt_at.isnot(None),
                NotificationEvent.next_attempt_at <= now,
            )
            .order_by(NotificationEvent.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(await self.session.scalars(statement))

    async def mark_sent(self, event: NotificationEvent, *, message_id: int | None) -> None:
        event.status = NotificationStatus.SENT
        event.telegram_message_id = message_id
        event.sent_at = utc_now()
        event.next_attempt_at = None
        await self.flush()

    async def mark_failed(
        self,
        event: NotificationEvent,
        *,
        error_code: str,
        retry_in_seconds: int | None,
        max_attempts: int,
    ) -> None:
        event.attempt_count += 1
        event.last_error_code = error_code[:64]
        if retry_in_seconds is None or event.attempt_count >= max_attempts:
            event.status = NotificationStatus.FAILED
            event.next_attempt_at = None
        else:
            event.next_attempt_at = utc_now() + timedelta(seconds=retry_in_seconds)
        await self.flush()

    async def mark_suppressed(self, event: NotificationEvent, *, reason: str) -> None:
        event.status = NotificationStatus.SUPPRESSED
        event.suppressed_reason = reason[:64]
        event.next_attempt_at = None
        await self.flush()

    async def reschedule(self, event: NotificationEvent, *, when: datetime) -> None:
        event.next_attempt_at = when
        await self.flush()

    async def list_for_user(
        self, user_id: uuid.UUID, *, page: int, page_size: int
    ) -> Page[NotificationEvent]:
        statement = (
            select(NotificationEvent)
            .where(NotificationEvent.telegram_user_id == user_id)
            .order_by(NotificationEvent.created_at.desc())
        )
        return await self.paginate(statement, page=page, page_size=page_size)

    async def list_all(
        self,
        *,
        page: int,
        page_size: int,
        status: NotificationStatus | None = None,
        user_id: uuid.UUID | None = None,
        event_type: NotificationEventType | None = None,
    ) -> Page[NotificationEvent]:
        statement: Select[tuple[NotificationEvent]] = select(NotificationEvent).order_by(
            NotificationEvent.created_at.desc()
        )
        if status is not None:
            statement = statement.where(NotificationEvent.status == status)
        if user_id is not None:
            statement = statement.where(NotificationEvent.telegram_user_id == user_id)
        if event_type is not None:
            statement = statement.where(NotificationEvent.event_type == event_type)
        return await self.paginate(statement, page=page, page_size=page_size)

    async def count_by_status(self) -> dict[NotificationStatus, int]:
        counts: dict[NotificationStatus, int] = {}
        for status in NotificationStatus:
            counts[status] = await self.count(
                select(NotificationEvent).where(NotificationEvent.status == status)
            )
        return counts

    async def purge_old(self, *, before: datetime) -> int:
        return await self.execute_dml(
            delete(NotificationEvent).where(
                NotificationEvent.created_at < before,
                NotificationEvent.status != NotificationStatus.PENDING,
            )
        )
