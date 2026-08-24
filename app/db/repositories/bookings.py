"""Repositories for booking attempts, cancellations and the appointment cache."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError

from app.db.models.bookings import AppointmentCache, BookingAttempt, CancellationAttempt
from app.db.repositories.base import BaseRepository, Page, utc_now
from app.domain.entities import PortalAppointment
from app.domain.enums import AppointmentStatus, BookingAttemptStatus, BookingStage
from app.domain.errors import ConflictError


class BookingAttemptRepository(BaseRepository[BookingAttempt]):
    model = BookingAttempt

    async def get_by_idempotency_key(self, key: str) -> BookingAttempt | None:
        return await self.one_or_none(
            select(BookingAttempt).where(BookingAttempt.idempotency_key == key)
        )

    async def start(
        self,
        *,
        user_id: uuid.UUID,
        account_id: uuid.UUID,
        patient_id: uuid.UUID | None,
        idempotency_key: str,
        slot_fingerprint: str,
        watch_id: uuid.UUID | None = None,
        draft_id: uuid.UUID | None = None,
        initiated_by_admin_id: uuid.UUID | None = None,
        correlation_id: str | None = None,
        slot_starts_at: datetime | None = None,
        location_name: str | None = None,
    ) -> tuple[BookingAttempt, bool]:
        """Create the attempt row, or return the existing one.

        The boolean reports whether this call created the attempt; a duplicate
        Telegram callback or a worker retry therefore never books twice.
        """
        existing = await self.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            return existing, False
        attempt = BookingAttempt(
            telegram_user_id=user_id,
            portal_account_id=account_id,
            patient_id=patient_id,
            watch_id=watch_id,
            draft_id=draft_id,
            initiated_by_admin_id=initiated_by_admin_id,
            idempotency_key=idempotency_key,
            slot_fingerprint=slot_fingerprint,
            status=BookingAttemptStatus.STARTED,
            stage=BookingStage.CREATED,
            correlation_id=None if correlation_id is None else correlation_id[:32],
            slot_starts_at=slot_starts_at,
            location_name=None if location_name is None else location_name[:255],
            started_at=utc_now(),
        )
        if not await self.try_add(attempt):
            duplicate = await self.get_by_idempotency_key(idempotency_key)
            if duplicate is None:  # pragma: no cover - defensive
                raise ConflictError("duplicate booking attempt could not be resolved")
            return duplicate, False
        return attempt, True

    async def advance(self, attempt: BookingAttempt, stage: BookingStage) -> None:
        attempt.stage = stage
        await self.flush()

    async def mark_confirm_sent(self, attempt: BookingAttempt) -> None:
        attempt.confirm_sent_at = utc_now()
        attempt.status = BookingAttemptStatus.VERIFYING
        attempt.stage = BookingStage.CONFIRM_SENT
        await self.flush()

    async def finish(
        self,
        attempt: BookingAttempt,
        *,
        status: BookingAttemptStatus,
        appointment_external_id: str | None = None,
        error_code: str | None = None,
        error_detail: str | None = None,
        watch_slot_success_key: str | None = None,
    ) -> None:
        attempt.status = status
        attempt.stage = BookingStage.RECONCILED
        attempt.finished_at = utc_now()
        attempt.portal_appointment_external_id = (
            None if appointment_external_id is None else appointment_external_id[:64]
        )
        attempt.error_code = None if error_code is None else error_code[:64]
        attempt.error_detail = None if error_detail is None else error_detail[:512]
        if status is BookingAttemptStatus.SUCCEEDED and watch_slot_success_key:
            attempt.watch_slot_success_key = watch_slot_success_key[:96]
        try:
            async with self.session.begin_nested():
                await self.session.flush()
        except IntegrityError as exc:
            raise ConflictError("this watch already booked the same slot") from exc

    async def has_successful_for_watch_slot(self, key: str) -> bool:
        found = await self.one_or_none(
            select(BookingAttempt).where(BookingAttempt.watch_slot_success_key == key[:96])
        )
        return found is not None

    async def list_for_user(
        self, user_id: uuid.UUID, *, page: int, page_size: int
    ) -> Page[BookingAttempt]:
        statement = (
            select(BookingAttempt)
            .where(BookingAttempt.telegram_user_id == user_id)
            .order_by(BookingAttempt.started_at.desc())
        )
        return await self.paginate(statement, page=page, page_size=page_size)

    async def list_all(
        self,
        *,
        page: int,
        page_size: int,
        status: BookingAttemptStatus | None = None,
        user_id: uuid.UUID | None = None,
        account_id: uuid.UUID | None = None,
    ) -> Page[BookingAttempt]:
        statement: Select[tuple[BookingAttempt]] = select(BookingAttempt).order_by(
            BookingAttempt.started_at.desc()
        )
        if status is not None:
            statement = statement.where(BookingAttempt.status == status)
        if user_id is not None:
            statement = statement.where(BookingAttempt.telegram_user_id == user_id)
        if account_id is not None:
            statement = statement.where(BookingAttempt.portal_account_id == account_id)
        return await self.paginate(statement, page=page, page_size=page_size)

    async def list_unreconciled(self, *, older_than: datetime) -> list[BookingAttempt]:
        """Attempts whose confirmation was sent but never reconciled."""
        statement = select(BookingAttempt).where(
            BookingAttempt.status == BookingAttemptStatus.VERIFYING,
            BookingAttempt.confirm_sent_at.isnot(None),
            BookingAttempt.confirm_sent_at < older_than,
        )
        result = await self.session.scalars(statement)
        return list(result)

    async def count_by_status(self) -> dict[BookingAttemptStatus, int]:
        counts: dict[BookingAttemptStatus, int] = {}
        for status in BookingAttemptStatus:
            counts[status] = await self.count(
                select(BookingAttempt).where(BookingAttempt.status == status)
            )
        return counts


class CancellationAttemptRepository(BaseRepository[CancellationAttempt]):
    model = CancellationAttempt

    async def start(
        self,
        *,
        user_id: uuid.UUID,
        account_id: uuid.UUID,
        appointment_id: uuid.UUID,
        idempotency_key: str,
        correlation_id: str | None = None,
        initiated_by_admin_id: uuid.UUID | None = None,
    ) -> tuple[CancellationAttempt, bool]:
        existing = await self.one_or_none(
            select(CancellationAttempt).where(
                CancellationAttempt.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            return existing, False
        record = CancellationAttempt(
            telegram_user_id=user_id,
            portal_account_id=account_id,
            appointment_id=appointment_id,
            idempotency_key=idempotency_key,
            initiated_by_admin_id=initiated_by_admin_id,
            correlation_id=None if correlation_id is None else correlation_id[:32],
            started_at=utc_now(),
        )
        if not await self.try_add(record):
            duplicate = await self.one_or_none(
                select(CancellationAttempt).where(
                    CancellationAttempt.idempotency_key == idempotency_key
                )
            )
            if duplicate is None:  # pragma: no cover - defensive
                raise ConflictError("duplicate cancellation attempt could not be resolved")
            return duplicate, False
        return record, True

    async def finish(
        self,
        record: CancellationAttempt,
        *,
        status: str,
        error_code: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        record.status = status[:24]
        record.error_code = None if error_code is None else error_code[:64]
        record.error_detail = None if error_detail is None else error_detail[:512]
        record.finished_at = utc_now()
        await self.flush()


class AppointmentRepository(BaseRepository[AppointmentCache]):
    model = AppointmentCache

    async def get_owned(
        self, appointment_id: uuid.UUID, user_id: uuid.UUID
    ) -> AppointmentCache | None:
        return await self.one_or_none(
            select(AppointmentCache).where(
                AppointmentCache.id == appointment_id,
                AppointmentCache.telegram_user_id == user_id,
                AppointmentCache.deleted_at.is_(None),
            )
        )

    async def get_any(self, appointment_id: uuid.UUID) -> AppointmentCache | None:
        return await self.session.get(AppointmentCache, appointment_id)

    async def find_by_external_id(
        self, account_id: uuid.UUID, external_id: str
    ) -> AppointmentCache | None:
        return await self.one_or_none(
            select(AppointmentCache).where(
                AppointmentCache.portal_account_id == account_id,
                AppointmentCache.portal_external_id == external_id[:64],
            )
        )

    async def upsert(
        self,
        *,
        user_id: uuid.UUID,
        account_id: uuid.UUID,
        patient_id: uuid.UUID | None,
        appointment: PortalAppointment,
        now: datetime,
    ) -> tuple[AppointmentCache, bool]:
        """Insert or refresh one appointment; the flag reports a new record."""
        record = await self.find_by_external_id(account_id, appointment.external_id)
        created = False
        if record is None:
            record = AppointmentCache(
                telegram_user_id=user_id,
                portal_account_id=account_id,
                patient_id=patient_id,
                portal_external_id=appointment.external_id[:64],
                first_seen_at=now,
                last_seen_at=now,
                raw_fingerprint=appointment.raw_fingerprint,
            )
            created = True
        record.patient_id = patient_id or record.patient_id
        record.service_name = appointment.service_name
        record.doctor_name = appointment.doctor_name
        record.location_name = appointment.location_name
        record.patient_label = appointment.patient_label
        record.scheduled_at_utc = (
            None
            if appointment.scheduled_at_utc is None
            else appointment.scheduled_at_utc.replace(tzinfo=None)
        )
        record.payment_note = (
            None if appointment.payment_note is None else appointment.payment_note[:255]
        )
        record.cancel_path = (
            None if appointment.cancel_path is None else appointment.cancel_path[:255]
        )
        record.pdf_available = appointment.pdf_path is not None
        record.raw_fingerprint = appointment.raw_fingerprint
        record.last_seen_at = now
        record.deleted_at = None
        record.status = _status_from_hint(appointment.status_hint)
        if record.status is AppointmentStatus.CANCELLED and record.cancelled_at is None:
            record.cancelled_at = now
        if created:
            self.session.add(record)
        await self.flush()
        return record, created

    async def mark_missing(
        self, *, account_id: uuid.UUID, seen_external_ids: Sequence[str], now: datetime
    ) -> list[AppointmentCache]:
        """Appointments that disappeared from the portal are marked cancelled."""
        seen = {value[:64] for value in seen_external_ids}
        statement = select(AppointmentCache).where(
            AppointmentCache.portal_account_id == account_id,
            AppointmentCache.status == AppointmentStatus.PENDING,
            AppointmentCache.deleted_at.is_(None),
        )
        vanished: list[AppointmentCache] = []
        for record in await self.session.scalars(statement):
            if record.portal_external_id not in seen:
                record.status = AppointmentStatus.CANCELLED
                record.cancelled_at = now
                vanished.append(record)
        await self.flush()
        return vanished

    async def list_upcoming(
        self,
        user_id: uuid.UUID,
        *,
        page: int,
        page_size: int,
        include_past: bool = False,
        now: datetime | None = None,
    ) -> Page[AppointmentCache]:
        statement = (
            select(AppointmentCache)
            .where(
                AppointmentCache.telegram_user_id == user_id,
                AppointmentCache.deleted_at.is_(None),
            )
            # MySQL has no NULLS LAST, so the null-ness is ordered explicitly.
            .order_by(
                AppointmentCache.scheduled_at_utc.is_(None).asc(),
                AppointmentCache.scheduled_at_utc.asc(),
            )
        )
        if not include_past:
            reference = now or utc_now()
            statement = statement.where(
                AppointmentCache.status == AppointmentStatus.PENDING,
                (AppointmentCache.scheduled_at_utc.is_(None))
                | (AppointmentCache.scheduled_at_utc >= reference),
            )
        else:
            statement = statement.order_by(None).order_by(
                AppointmentCache.scheduled_at_utc.is_(None).asc(),
                AppointmentCache.scheduled_at_utc.desc(),
            )
        return await self.paginate(statement, page=page, page_size=page_size)

    async def list_all(
        self,
        *,
        page: int,
        page_size: int,
        status: AppointmentStatus | None = None,
        user_id: uuid.UUID | None = None,
        account_id: uuid.UUID | None = None,
    ) -> Page[AppointmentCache]:
        statement = select(AppointmentCache).order_by(
            AppointmentCache.scheduled_at_utc.is_(None).asc(),
            AppointmentCache.scheduled_at_utc.desc(),
        )
        if status is not None:
            statement = statement.where(AppointmentCache.status == status)
        if user_id is not None:
            statement = statement.where(AppointmentCache.telegram_user_id == user_id)
        if account_id is not None:
            statement = statement.where(AppointmentCache.portal_account_id == account_id)
        return await self.paginate(statement, page=page, page_size=page_size)

    async def mark_cancelled(self, record: AppointmentCache, *, now: datetime) -> None:
        record.status = AppointmentStatus.CANCELLED
        record.cancelled_at = now
        await self.flush()

    async def register_cancellation_attempt(self, record: AppointmentCache) -> None:
        record.cancellation_attempts += 1
        await self.flush()

    async def purge_old(self, *, before: datetime) -> int:
        statement = select(AppointmentCache).where(
            AppointmentCache.status != AppointmentStatus.PENDING,
            AppointmentCache.last_seen_at < before,
        )
        removed = 0
        for record in await self.session.scalars(statement):
            await self.session.delete(record)
            removed += 1
        await self.flush()
        return removed


def _status_from_hint(hint: str | None) -> AppointmentStatus:
    """Map a portal status hint onto the internal appointment status."""
    if not hint:
        return AppointmentStatus.PENDING
    lowered = hint.strip().casefold()
    if any(marker in lowered for marker in ("annull", "cancel", "disdet")):
        return AppointmentStatus.CANCELLED
    if any(marker in lowered for marker in ("complet", "esegui", "effettuat", "done")):
        return AppointmentStatus.COMPLETED
    if any(marker in lowered for marker in ("pending", "attes", "confermat", "prenotat")):
        return AppointmentStatus.PENDING
    return AppointmentStatus.UNKNOWN
