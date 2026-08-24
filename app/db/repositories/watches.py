"""Repositories for monitors, their locations, seen slots and run logs."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Select, delete, select, update

from app.db.models.watches import BookingWatch, SeenSlot, WatchLocation, WatchRunLog
from app.db.repositories.base import BaseRepository, Page, utc_now
from app.domain.enums import WatchStatus


class WatchRepository(BaseRepository[BookingWatch]):
    model = BookingWatch

    async def get_owned(self, watch_id: uuid.UUID, user_id: uuid.UUID) -> BookingWatch | None:
        return await self.one_or_none(
            select(BookingWatch).where(
                BookingWatch.id == watch_id,
                BookingWatch.telegram_user_id == user_id,
                BookingWatch.deleted_at.is_(None),
            )
        )

    async def get_any(self, watch_id: uuid.UUID) -> BookingWatch | None:
        return await self.one_or_none(
            select(BookingWatch).where(
                BookingWatch.id == watch_id, BookingWatch.deleted_at.is_(None)
            )
        )

    def _user_statement(
        self, user_id: uuid.UUID, *, status: WatchStatus | None
    ) -> Select[tuple[BookingWatch]]:
        statement = (
            select(BookingWatch)
            .where(
                BookingWatch.telegram_user_id == user_id,
                BookingWatch.deleted_at.is_(None),
                BookingWatch.status != WatchStatus.DRAFT,
            )
            .order_by(BookingWatch.created_at.desc())
        )
        if status is not None:
            statement = statement.where(BookingWatch.status == status)
        return statement

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        page: int,
        page_size: int,
        status: WatchStatus | None = None,
    ) -> Page[BookingWatch]:
        return await self.paginate(
            self._user_statement(user_id, status=status), page=page, page_size=page_size
        )

    async def count_active_for_account(self, account_id: uuid.UUID) -> int:
        return await self.count(
            select(BookingWatch).where(
                BookingWatch.portal_account_id == account_id,
                BookingWatch.deleted_at.is_(None),
                BookingWatch.status.in_([WatchStatus.ACTIVE, WatchStatus.PAUSED]),
            )
        )

    async def list_all(
        self,
        *,
        page: int,
        page_size: int,
        status: WatchStatus | None = None,
        user_id: uuid.UUID | None = None,
        account_id: uuid.UUID | None = None,
    ) -> Page[BookingWatch]:
        statement = (
            select(BookingWatch)
            .where(BookingWatch.deleted_at.is_(None))
            .order_by(BookingWatch.created_at.desc())
        )
        if status is not None:
            statement = statement.where(BookingWatch.status == status)
        if user_id is not None:
            statement = statement.where(BookingWatch.telegram_user_id == user_id)
        if account_id is not None:
            statement = statement.where(BookingWatch.portal_account_id == account_id)
        return await self.paginate(statement, page=page, page_size=page_size)

    async def set_locations(
        self, watch: BookingWatch, locations: Sequence[tuple[str, str, int]]
    ) -> None:
        """Replace the explicit location selection of a monitor."""
        await self.session.execute(delete(WatchLocation).where(WatchLocation.watch_id == watch.id))
        for location_id, name, order in locations:
            self.session.add(
                WatchLocation(
                    watch_id=watch.id,
                    portal_location_id=location_id[:64],
                    location_name=name[:255],
                    preference_order=order,
                )
            )
        await self.flush()

    async def locations_for(self, watch_id: uuid.UUID) -> list[WatchLocation]:
        result = await self.session.scalars(
            select(WatchLocation)
            .where(WatchLocation.watch_id == watch_id)
            .order_by(WatchLocation.preference_order.asc(), WatchLocation.location_name.asc())
        )
        return list(result)

    async def claim_due(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
        limit: int,
    ) -> list[BookingWatch]:
        """Atomically lease the monitors that are due.

        ``FOR UPDATE SKIP LOCKED`` guarantees that two workers never pick the
        same row, and the lease expiry lets a crashed worker's monitors be
        recovered without any external coordination service.
        """
        from datetime import timedelta

        statement = (
            select(BookingWatch)
            .where(
                BookingWatch.status == WatchStatus.ACTIVE,
                BookingWatch.deleted_at.is_(None),
                BookingWatch.next_check_at.isnot(None),
                BookingWatch.next_check_at <= now,
                (BookingWatch.lease_expires_at.is_(None)) | (BookingWatch.lease_expires_at <= now),
            )
            .order_by(BookingWatch.next_check_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        candidates = list(await self.session.scalars(statement))
        leased: list[BookingWatch] = []
        expiry = now + timedelta(seconds=lease_seconds)
        for watch in candidates:
            watch.lease_owner = worker_id[:64]
            watch.lease_expires_at = expiry
            leased.append(watch)
        await self.flush()
        return leased

    async def renew_lease(self, watch: BookingWatch, *, worker_id: str, lease_seconds: int) -> bool:
        """Extend a lease only if this worker still owns it."""
        from datetime import timedelta

        if watch.lease_owner != worker_id[:64]:
            return False
        watch.lease_expires_at = utc_now() + timedelta(seconds=lease_seconds)
        await self.flush()
        return True

    async def release_lease(self, watch: BookingWatch, *, next_check_at: datetime) -> None:
        watch.lease_owner = None
        watch.lease_expires_at = None
        watch.next_check_at = next_check_at
        await self.flush()

    async def release_leases_of_worker(self, worker_id: str) -> int:
        return await self.execute_dml(
            update(BookingWatch)
            .where(BookingWatch.lease_owner == worker_id[:64])
            .values(lease_owner=None, lease_expires_at=None)
        )

    async def set_status(
        self, watch: BookingWatch, status: WatchStatus, *, reason: str | None = None
    ) -> None:
        watch.status = status
        if reason is not None:
            watch.completed_reason = reason[:64]
        if status is not WatchStatus.ACTIVE:
            watch.lease_owner = None
            watch.lease_expires_at = None
        await self.flush()

    async def soft_delete(self, watch: BookingWatch) -> None:
        watch.status = WatchStatus.DELETED
        watch.deleted_at = utc_now()
        watch.lease_owner = None
        watch.lease_expires_at = None
        watch.next_check_at = None
        await self.flush()

    async def record_success(
        self, watch: BookingWatch, *, now: datetime, next_check_at: datetime
    ) -> None:
        watch.last_checked_at = now
        watch.last_success_at = now
        watch.consecutive_errors = 0
        watch.last_error_code = None
        watch.next_check_at = next_check_at
        watch.lease_owner = None
        watch.lease_expires_at = None
        await self.flush()

    async def record_error(
        self,
        watch: BookingWatch,
        *,
        now: datetime,
        next_check_at: datetime,
        error_code: str,
        max_errors: int,
    ) -> bool:
        """Record a failed run and return ``True`` when the monitor was suspended."""
        watch.last_checked_at = now
        watch.consecutive_errors += 1
        watch.last_error_code = error_code[:64]
        watch.last_error_at = now
        watch.next_check_at = next_check_at
        watch.lease_owner = None
        watch.lease_expires_at = None
        suspended = watch.consecutive_errors >= max_errors
        if suspended:
            watch.status = WatchStatus.ERROR
            watch.next_check_at = None
        await self.flush()
        return suspended

    async def suspend_for_account(
        self, account_id: uuid.UUID, *, reason: str
    ) -> list[BookingWatch]:
        statement = select(BookingWatch).where(
            BookingWatch.portal_account_id == account_id,
            BookingWatch.status == WatchStatus.ACTIVE,
            BookingWatch.deleted_at.is_(None),
        )
        suspended: list[BookingWatch] = []
        for watch in await self.session.scalars(statement):
            watch.status = WatchStatus.PAUSED
            watch.completed_reason = reason[:64]
            watch.lease_owner = None
            watch.lease_expires_at = None
            suspended.append(watch)
        await self.flush()
        return suspended

    async def due_count(self, *, now: datetime) -> int:
        return await self.count(
            select(BookingWatch).where(
                BookingWatch.status == WatchStatus.ACTIVE,
                BookingWatch.deleted_at.is_(None),
                BookingWatch.next_check_at.isnot(None),
                BookingWatch.next_check_at <= now,
            )
        )

    async def leased_count(self, *, now: datetime) -> int:
        return await self.count(
            select(BookingWatch).where(
                BookingWatch.lease_expires_at.isnot(None),
                BookingWatch.lease_expires_at > now,
            )
        )


class SeenSlotRepository(BaseRepository[SeenSlot]):
    model = SeenSlot

    async def fingerprints_for(self, watch_id: uuid.UUID) -> dict[str, SeenSlot]:
        result = await self.session.scalars(select(SeenSlot).where(SeenSlot.watch_id == watch_id))
        return {record.slot_fingerprint: record for record in result}

    async def register(
        self,
        *,
        watch_id: uuid.UUID,
        fingerprint: str,
        now: datetime,
        slot_starts_at: datetime | None,
        location_id: str | None,
        location_name: str | None,
        existing: SeenSlot | None,
    ) -> tuple[SeenSlot, bool]:
        """Insert or refresh a seen slot; the flag reports whether it is new."""
        if existing is not None:
            was_absent = not existing.currently_available
            existing.last_seen_at = now
            existing.currently_available = True
            await self.flush()
            return existing, was_absent
        record = SeenSlot(
            watch_id=watch_id,
            slot_fingerprint=fingerprint,
            first_seen_at=now,
            last_seen_at=now,
            currently_available=True,
            slot_starts_at=slot_starts_at,
            location_id=None if location_id is None else location_id[:64],
            location_name=None if location_name is None else location_name[:255],
        )
        return await self.add(record), True

    async def mark_notified(self, record: SeenSlot, *, now: datetime) -> None:
        record.last_notified_at = now
        await self.flush()

    async def mark_absent(
        self, *, watch_id: uuid.UUID, present_fingerprints: set[str]
    ) -> list[SeenSlot]:
        statement = select(SeenSlot).where(
            SeenSlot.watch_id == watch_id, SeenSlot.currently_available.is_(True)
        )
        disappeared: list[SeenSlot] = []
        for record in await self.session.scalars(statement):
            if record.slot_fingerprint not in present_fingerprints:
                record.currently_available = False
                disappeared.append(record)
        await self.flush()
        return disappeared

    async def clear_for_watch(self, watch_id: uuid.UUID) -> int:
        """Forget every slot already observed by one monitor."""
        return await self.execute_dml(delete(SeenSlot).where(SeenSlot.watch_id == watch_id))

    async def purge_old(self, *, before: datetime) -> int:
        return await self.execute_dml(delete(SeenSlot).where(SeenSlot.last_seen_at < before))

    async def list_for_watch(
        self, watch_id: uuid.UUID, *, page: int, page_size: int
    ) -> Page[SeenSlot]:
        statement = (
            select(SeenSlot)
            .where(SeenSlot.watch_id == watch_id)
            .order_by(SeenSlot.last_seen_at.desc())
        )
        return await self.paginate(statement, page=page, page_size=page_size)


class WatchRunLogRepository(BaseRepository[WatchRunLog]):
    model = WatchRunLog

    async def start(
        self, *, watch_id: uuid.UUID, now: datetime, worker_id: str, correlation_id: str
    ) -> WatchRunLog:
        record = WatchRunLog(
            watch_id=watch_id,
            started_at=now,
            outcome="RUNNING",
            worker_id=worker_id[:64],
            correlation_id=correlation_id[:32],
        )
        return await self.add(record)

    async def finish(
        self,
        record: WatchRunLog,
        *,
        outcome: str,
        slots_found: int = 0,
        new_slots: int = 0,
        error_code: str | None = None,
        now: datetime | None = None,
    ) -> None:
        finished = now or utc_now()
        record.finished_at = finished
        record.outcome = outcome[:32]
        record.slots_found = slots_found
        record.new_slots = new_slots
        record.error_code = None if error_code is None else error_code[:64]
        record.duration_ms = int((finished - record.started_at).total_seconds() * 1000)
        await self.flush()

    async def list_for_watch(
        self, watch_id: uuid.UUID, *, page: int, page_size: int
    ) -> Page[WatchRunLog]:
        statement = (
            select(WatchRunLog)
            .where(WatchRunLog.watch_id == watch_id)
            .order_by(WatchRunLog.started_at.desc())
        )
        return await self.paginate(statement, page=page, page_size=page_size)

    async def purge_old(self, *, before: datetime) -> int:
        return await self.execute_dml(delete(WatchRunLog).where(WatchRunLog.started_at < before))
