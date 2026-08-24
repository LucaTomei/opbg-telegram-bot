"""Idempotent booking protocol shared by manual booking and auto-booking.

The sequence is deliberately conservative: the attempt row is created before
any portal traffic, the account lock serialises mutating sequences, the final
confirmation is sent exactly once and success is only declared after the
appointment list has been reloaded and reconciled.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models.accounts import Patient, PortalAccount
from app.db.models.bookings import BookingAttempt
from app.db.repositories.base import utc_now
from app.db.repositories.bookings import AppointmentRepository, BookingAttemptRepository
from app.domain.entities import (
    BookingOutcome,
    PortalAppointment,
    PortalSlot,
    SearchSelection,
    SlotFilter,
)
from app.domain.enums import BookingAttemptStatus, BookingStage, LocationMode
from app.domain.errors import (
    BookingAmbiguous,
    BookingRejected,
    LockUnavailableError,
    SlotNoLongerAvailable,
)
from app.domain.filters import apply_filter, deduplicate
from app.domain.fingerprint import (
    idempotency_key,
    search_key,
    slot_fingerprint,
    snapshot_hash,
    watch_slot_success_key,
)
from app.logging import get_logger, new_correlation_id
from app.portal.client import PortalClient
from app.services.locks import account_lock
from app.services.search import SearchService

logger = get_logger(__name__)

BOOKING_LOCK_TIMEOUT_SECONDS = 15


@dataclass(frozen=True, slots=True)
class BookingRequest:
    """Everything needed to attempt one booking."""

    account: PortalAccount
    patient: Patient
    selection: SearchSelection
    slot_filter: SlotFilter
    slot: PortalSlot
    user_id: uuid.UUID
    watch_id: uuid.UUID | None = None
    draft_id: uuid.UUID | None = None
    initiated_by_admin_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class BookingResult:
    """Result of a booking attempt together with its persisted record."""

    attempt: BookingAttempt
    outcome: BookingOutcome
    duplicate: bool


class BookingService:
    """Executes the idempotent booking protocol."""

    def __init__(
        self,
        settings: Settings,
        client: PortalClient,
        search: SearchService,
    ) -> None:
        self._settings = settings
        self._client = client
        self._search = search

    def fingerprint_for(self, request: BookingRequest) -> str:
        return slot_fingerprint(
            request.slot,
            account_id=request.account.id,
            patient_id=request.patient.id,
            search_key=search_key(
                search_type=request.selection.search_type.value,
                service_id=request.selection.service_id,
                doctor_id=request.selection.doctor_id,
            ),
        )

    async def book(
        self, session: AsyncSession, request: BookingRequest, *, now: datetime | None = None
    ) -> BookingResult:
        """Attempt to book ``request.slot`` exactly once."""
        moment = now or utc_now()
        attempts = BookingAttemptRepository(session)
        fingerprint = self.fingerprint_for(request)
        scope = "watch" if request.watch_id else "manual"
        key = idempotency_key(
            account_id=request.account.id,
            patient_id=request.patient.id,
            fingerprint=fingerprint,
            scope=f"{scope}:{request.watch_id.hex if request.watch_id else request.user_id.hex}",
        )
        correlation_id = new_correlation_id()
        attempt, created = await attempts.start(
            user_id=request.user_id,
            account_id=request.account.id,
            patient_id=request.patient.id,
            idempotency_key=key,
            slot_fingerprint=fingerprint,
            watch_id=request.watch_id,
            draft_id=request.draft_id,
            initiated_by_admin_id=request.initiated_by_admin_id,
            correlation_id=correlation_id,
            slot_starts_at=request.slot.starts_at_utc.replace(tzinfo=None),
            location_name=request.slot.location_name,
        )
        if not created:
            return BookingResult(
                attempt=attempt,
                outcome=self._outcome_from_attempt(attempt),
                duplicate=True,
            )

        if request.watch_id is not None:
            success_key = watch_slot_success_key(request.watch_id, fingerprint)
            if await attempts.has_successful_for_watch_slot(success_key):
                await attempts.finish(
                    attempt,
                    status=BookingAttemptStatus.SLOT_LOST,
                    error_code="ALREADY_BOOKED",
                )
                return BookingResult(
                    attempt=attempt,
                    outcome=BookingOutcome(
                        succeeded=False,
                        ambiguous=False,
                        appointment_external_id=None,
                        detail="questo slot è già stato prenotato da questo monitoraggio",
                    ),
                    duplicate=True,
                )

        try:
            async with account_lock(
                session, request.account.id, timeout_seconds=BOOKING_LOCK_TIMEOUT_SECONDS
            ):
                outcome = await self._execute(session, request, attempt, fingerprint, moment)
        except LockUnavailableError:
            await attempts.finish(
                attempt,
                status=BookingAttemptStatus.FAILED,
                error_code="LOCK_UNAVAILABLE",
                error_detail="another booking is already running for this account",
            )
            raise
        return BookingResult(attempt=attempt, outcome=outcome, duplicate=False)

    async def _execute(
        self,
        session: AsyncSession,
        request: BookingRequest,
        attempt: BookingAttempt,
        fingerprint: str,
        moment: datetime,
    ) -> BookingOutcome:
        attempts = BookingAttemptRepository(session)
        appointments = AppointmentRepository(session)

        async with self._search.availability_flow(
            session, request.account, request.patient, request.selection
        ) as flow:
            await attempts.advance(attempt, BookingStage.PATIENT_SELECTED)
            requested_locations = (
                ()
                if request.slot_filter.location_mode is LocationMode.ALL_DYNAMIC
                else tuple(request.slot_filter.location_ids)
            )
            availability = await self._search.fetch_availability(
                flow, location_ids=requested_locations
            )
            await attempts.advance(attempt, BookingStage.AVAILABILITY_REFRESHED)

            fresh = self._match_slot(request, availability.slots, fingerprint, moment)
            if fresh is None:
                await attempts.finish(
                    attempt,
                    status=BookingAttemptStatus.SLOT_LOST,
                    error_code=SlotNoLongerAvailable.code,
                )
                return BookingOutcome(
                    succeeded=False,
                    ambiguous=False,
                    appointment_external_id=None,
                    detail="lo slot non è più disponibile",
                )

            before = await self._client.list_appointments(flow.portal_session)
            before_ids = [item.external_id for item in before]
            attempt.snapshot_before_hash = snapshot_hash(before_ids)

            await self._client.select_slot(flow.portal_session, fresh)
            await attempts.advance(attempt, BookingStage.SLOT_SELECTED)

            await attempts.mark_confirm_sent(attempt)
            try:
                await self._client.confirm_booking(flow.portal_session)
            except BookingRejected as exc:
                await attempts.finish(
                    attempt, status=BookingAttemptStatus.REJECTED, error_code=exc.code
                )
                return BookingOutcome(
                    succeeded=False,
                    ambiguous=False,
                    appointment_external_id=None,
                    detail="il portale ha rifiutato la prenotazione",
                )
            except Exception as exc:
                # The confirmation may have been processed anyway: reconcile
                # instead of retrying, then decide.
                logger.warning(
                    "confirmation failed, reconciling the appointment list",
                    extra={
                        "event_code": "BOOKING_CONFIRM_ERROR",
                        "attempt_id": attempt.id.hex,
                        "error_type": type(exc).__name__,
                    },
                )

            after = await self._client.list_appointments(flow.portal_session)
            attempt.snapshot_after_hash = snapshot_hash([item.external_id for item in after])
            matched = self._reconcile(before_ids, after, fresh, request)

        if matched is None:
            await attempts.finish(
                attempt,
                status=BookingAttemptStatus.AMBIGUOUS,
                error_code=BookingAmbiguous.code,
                error_detail="the appointment list did not confirm the booking",
            )
            logger.error(
                "booking outcome is ambiguous",
                extra={
                    "event_code": "BOOKING_AMBIGUOUS",
                    "attempt_id": attempt.id.hex,
                    "account_id": request.account.id.hex,
                },
            )
            return BookingOutcome(
                succeeded=False,
                ambiguous=True,
                appointment_external_id=None,
                detail="esito incerto: controlla i tuoi appuntamenti prima di riprovare",
            )

        await appointments.upsert(
            user_id=request.user_id,
            account_id=request.account.id,
            patient_id=request.patient.id,
            appointment=matched,
            now=moment,
        )
        success_key = (
            watch_slot_success_key(request.watch_id, fingerprint)
            if request.watch_id is not None
            else None
        )
        await attempts.finish(
            attempt,
            status=BookingAttemptStatus.SUCCEEDED,
            appointment_external_id=matched.external_id,
            watch_slot_success_key=success_key,
        )
        logger.info(
            "booking confirmed and reconciled",
            extra={
                "event_code": "BOOKING_SUCCEEDED",
                "attempt_id": attempt.id.hex,
                "account_id": request.account.id.hex,
            },
        )
        return BookingOutcome(
            succeeded=True,
            ambiguous=False,
            appointment_external_id=matched.external_id,
            detail="prenotazione confermata",
            appointment=matched,
        )

    def _match_slot(
        self,
        request: BookingRequest,
        slots: tuple[PortalSlot, ...],
        fingerprint: str,
        moment: datetime,
    ) -> PortalSlot | None:
        """Find the requested slot again and re-check it against the filters."""
        candidates = deduplicate(slots)
        allowed = apply_filter(candidates, request.slot_filter, now=moment)
        allowed_keys = {
            slot_fingerprint(
                slot,
                account_id=request.account.id,
                patient_id=request.patient.id,
                search_key=search_key(
                    search_type=request.selection.search_type.value,
                    service_id=request.selection.service_id,
                    doctor_id=request.selection.doctor_id,
                ),
            ): slot
            for slot in allowed
        }
        return allowed_keys.get(fingerprint)

    @staticmethod
    def _reconcile(
        before_ids: list[str],
        after: tuple[PortalAppointment, ...],
        slot: PortalSlot,
        request: BookingRequest,
    ) -> PortalAppointment | None:
        """Identify the appointment created by this attempt, or return ``None``."""
        known = set(before_ids)
        new_items = [item for item in after if item.external_id not in known]
        if not new_items:
            return None
        target_utc = slot.starts_at_utc.replace(tzinfo=None)
        for item in new_items:
            if item.scheduled_at_utc is None:
                continue
            scheduled = item.scheduled_at_utc.replace(tzinfo=None)
            if abs((scheduled - target_utc).total_seconds()) > 60:
                continue
            if (
                request.selection.service_name
                and item.service_name
                and _loose_match(item.service_name, request.selection.service_name) is False
            ):
                continue
            return item
        if len(new_items) == 1 and new_items[0].scheduled_at_utc is None:
            return new_items[0]
        return None

    @staticmethod
    def _outcome_from_attempt(attempt: BookingAttempt) -> BookingOutcome:
        if attempt.status is BookingAttemptStatus.SUCCEEDED:
            return BookingOutcome(
                succeeded=True,
                ambiguous=False,
                appointment_external_id=attempt.portal_appointment_external_id,
                detail="prenotazione già confermata",
            )
        if attempt.status is BookingAttemptStatus.AMBIGUOUS:
            return BookingOutcome(
                succeeded=False,
                ambiguous=True,
                appointment_external_id=None,
                detail="esito incerto già registrato: nessun nuovo tentativo eseguito",
            )
        if attempt.status is BookingAttemptStatus.SLOT_LOST:
            return BookingOutcome(
                succeeded=False,
                ambiguous=False,
                appointment_external_id=None,
                detail="lo slot non era più disponibile",
            )
        return BookingOutcome(
            succeeded=False,
            ambiguous=False,
            appointment_external_id=None,
            detail="tentativo già in corso o concluso",
        )


def _loose_match(left: str, right: str) -> bool:
    """Compare two portal descriptions ignoring case, accents and spacing."""
    from app.services.search import normalize_for_match

    normalized_left = normalize_for_match(left)
    normalized_right = normalize_for_match(right)
    return normalized_left in normalized_right or normalized_right in normalized_left
