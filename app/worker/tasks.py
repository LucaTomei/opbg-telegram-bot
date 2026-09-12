"""Worker tasks: monitor execution, appointment sync, notifications, cleanup."""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models.accounts import PortalAccount
from app.db.models.watches import BookingWatch
from app.db.repositories.accounts import (
    ConnectRateLimitRepository,
    PatientRepository,
    PortalAccountRepository,
)
from app.db.repositories.audit import AuditRepository
from app.db.repositories.base import utc_now
from app.db.repositories.bookings import AppointmentRepository, BookingAttemptRepository
from app.db.repositories.conversations import (
    BookingDraftRepository,
    CallbackConsumptionRepository,
    ConfirmationNonceRepository,
    ConversationStateRepository,
)
from app.db.repositories.notifications import NotificationRepository
from app.db.repositories.system import (
    HeartbeatRepository,
    PortalCallLogRepository,
    SystemEventRepository,
)
from app.db.repositories.users import InviteRepository, UserRepository
from app.db.repositories.watches import (
    SeenSlotRepository,
    WatchRepository,
    WatchRunLogRepository,
)
from app.domain.entities import PortalSlot
from app.domain.enums import (
    BookingAttemptStatus,
    NotificationEventType,
    PortalAccountStatus,
    Severity,
    UserStatus,
    WatchCompletionPolicy,
    WatchStatus,
)
from app.domain.errors import (
    AccountSuspendedError,
    AmbiguousSearchResult,
    AuthenticationFailed,
    CircuitOpenError,
    DomainError,
    LockUnavailableError,
    PortalContractChanged,
    ReauthenticationRequired,
)
from app.domain.filters import next_check_at
from app.domain.fingerprint import search_key, slot_fingerprint
from app.logging import correlation_scope, get_logger
from app.services.accounts import REASON_REAUTH_REQUIRED
from app.services.bookings import BookingRequest, BookingService
from app.services.mapping import watch_filter, watch_selection
from app.services.notifications import NotificationService
from app.services.portal_sessions import PortalSessionManager, reauth_subject
from app.services.search import SearchService

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class WatchRunOutcome:
    """Result of a single monitor execution."""

    outcome: str
    slots_found: int
    new_slots: int
    booked: bool
    error_code: str | None = None


class WatchProcessor:
    """Executes one monitor: search, filter, notify and optionally auto-book."""

    def __init__(
        self,
        settings: Settings,
        search: SearchService,
        booking: BookingService,
        notifications: NotificationService,
        *,
        jitter: random.Random | None = None,
    ) -> None:
        self._settings = settings
        self._search = search
        self._booking = booking
        self._notifications = notifications
        self._random = jitter or random.Random()

    def _reschedule(self, watch: BookingWatch, *, now: datetime, errors: int) -> datetime:
        return next_check_at(
            now=now,
            interval_seconds=watch.interval_seconds,
            jitter_seconds=self._settings.monitor_jitter_seconds,
            consecutive_errors=errors,
            backoff_base_seconds=self._settings.portal_backoff_base_seconds,
            backoff_max_seconds=self._settings.monitor_error_backoff_max_seconds,
            jitter_value=self._random.random(),
        )

    async def run(
        self, session: AsyncSession, watch: BookingWatch, *, worker_id: str
    ) -> WatchRunOutcome:
        """Run one monitor end to end, never letting one failure stop the others."""
        watches = WatchRepository(session)
        runs = WatchRunLogRepository(session)
        now = utc_now()
        with correlation_scope() as correlation_id:
            run_log = await runs.start(
                watch_id=watch.id,
                now=now,
                worker_id=worker_id,
                correlation_id=correlation_id,
            )
            try:
                result = await self._run_inner(session, watch, now=now)
            except (AuthenticationFailed, ReauthenticationRequired, AccountSuspendedError) as exc:
                await self._suspend_for_reauth(session, watch, error_code=exc.code)
                await runs.finish(run_log, outcome="REAUTH_REQUIRED", error_code=exc.code)
                return WatchRunOutcome(
                    outcome="REAUTH_REQUIRED",
                    slots_found=0,
                    new_slots=0,
                    booked=False,
                    error_code=exc.code,
                )
            except AmbiguousSearchResult as exc:
                await self._suspend_for_ambiguity(session, watch, error_code=exc.code)
                await runs.finish(run_log, outcome="AMBIGUOUS_SEARCH", error_code=exc.code)
                return WatchRunOutcome(
                    outcome="AMBIGUOUS_SEARCH",
                    slots_found=0,
                    new_slots=0,
                    booked=False,
                    error_code=exc.code,
                )
            except (CircuitOpenError, LockUnavailableError) as exc:
                retry_at = now + timedelta(
                    seconds=max(int(getattr(exc, "retry_after_seconds", 60) or 60), 60)
                )
                await watches.release_lease(watch, next_check_at=retry_at)
                await runs.finish(run_log, outcome="DEFERRED", error_code=exc.code)
                return WatchRunOutcome(
                    outcome="DEFERRED",
                    slots_found=0,
                    new_slots=0,
                    booked=False,
                    error_code=exc.code,
                )
            except DomainError as exc:
                suspended = await watches.record_error(
                    watch,
                    now=now,
                    next_check_at=self._reschedule(
                        watch, now=now, errors=watch.consecutive_errors + 1
                    ),
                    error_code=exc.code,
                    max_errors=self._settings.monitor_max_consecutive_errors,
                )
                await SystemEventRepository(session).record(
                    severity=Severity.ERROR
                    if isinstance(exc, PortalContractChanged)
                    else Severity.WARNING,
                    component="worker",
                    event_code=exc.code,
                    message=f"monitor execution failed: {exc.detail}",
                    correlation_id=correlation_id,
                    portal_account_id=watch.portal_account_id,
                    telegram_user_id=watch.telegram_user_id,
                )
                if suspended:
                    await self._notify_suspended(session, watch, reason=exc.code)
                await runs.finish(run_log, outcome="ERROR", error_code=exc.code)
                return WatchRunOutcome(
                    outcome="ERROR",
                    slots_found=0,
                    new_slots=0,
                    booked=False,
                    error_code=exc.code,
                )
            await runs.finish(
                run_log,
                outcome=result.outcome,
                slots_found=result.slots_found,
                new_slots=result.new_slots,
            )
            return result

    async def _run_inner(
        self, session: AsyncSession, watch: BookingWatch, *, now: datetime
    ) -> WatchRunOutcome:
        watches = WatchRepository(session)
        accounts = PortalAccountRepository(session)
        patients = PatientRepository(session)
        users = UserRepository(session)
        seen_slots = SeenSlotRepository(session)

        user = await users.get(watch.telegram_user_id)
        account = await accounts.get(watch.portal_account_id)
        patient = await patients.get(watch.patient_id)
        if user is None or account is None or patient is None:
            await watches.set_status(watch, WatchStatus.ERROR, reason="MISSING_REFERENCE")
            return WatchRunOutcome(
                outcome="MISSING_REFERENCE",
                slots_found=0,
                new_slots=0,
                booked=False,
                error_code="MISSING_REFERENCE",
            )
        if user.status is not UserStatus.ACTIVE:
            await watches.set_status(watch, WatchStatus.PAUSED, reason="USER_NOT_ACTIVE")
            return WatchRunOutcome(
                outcome="USER_NOT_ACTIVE", slots_found=0, new_slots=0, booked=False
            )
        if account.status is not PortalAccountStatus.ACTIVE:
            await watches.set_status(watch, WatchStatus.PAUSED, reason="ACCOUNT_NOT_ACTIVE")
            return WatchRunOutcome(
                outcome="ACCOUNT_NOT_ACTIVE", slots_found=0, new_slots=0, booked=False
            )

        locations = await watches.locations_for(watch.id)
        slot_filter = watch_filter(watch, locations)
        selection = watch_selection(watch)
        result = await self._search.collect(
            session, account, patient, selection, slot_filter, now=now
        )
        await self._report_missing_locations(session, watch, result, now=now)

        key = search_key(
            search_type=selection.search_type.value,
            service_id=selection.service_id,
            doctor_id=selection.doctor_id,
        )
        known = await seen_slots.fingerprints_for(watch.id)
        fresh: list[tuple[str, PortalSlot]] = []
        present: set[str] = set()
        for slot in result.slots:
            fingerprint = slot_fingerprint(
                slot, account_id=account.id, patient_id=patient.id, search_key=key
            )
            present.add(fingerprint)
            _, is_new = await seen_slots.register(
                watch_id=watch.id,
                fingerprint=fingerprint,
                now=now,
                slot_starts_at=slot.starts_at_utc.replace(tzinfo=None),
                location_id=slot.location_id,
                location_name=slot.location_name,
                existing=known.get(fingerprint),
            )
            if is_new:
                fresh.append((fingerprint, slot))
        await seen_slots.mark_absent(watch_id=watch.id, present_fingerprints=present)

        booked = False
        notify_candidates = (
            fresh
            if watch.notify_only_changes
            else [
                (
                    slot_fingerprint(
                        slot, account_id=account.id, patient_id=patient.id, search_key=key
                    ),
                    slot,
                )
                for slot in result.slots
            ]
        )

        if watch.mode.books and result.slots:
            booked = await self._try_autobook(
                session,
                watch,
                account=account,
                patient=patient,
                selection=selection,
                slot_filter=slot_filter,
                slot=result.slots[0],
                user_id=user.id,
                now=now,
            )

        if watch.mode.notifies_availability and notify_candidates and not booked:
            await self._notify_new_slots(
                session, watch, user=user, candidates=notify_candidates, now=now
            )

        if booked:
            await watches.record_success(
                watch, now=now, next_check_at=self._reschedule(watch, now=now, errors=0)
            )
            if watch.completion_policy is WatchCompletionPolicy.COMPLETE_ON_SUCCESS:
                await watches.set_status(watch, WatchStatus.COMPLETED, reason="BOOKED")
        else:
            await watches.record_success(
                watch, now=now, next_check_at=self._reschedule(watch, now=now, errors=0)
            )
        watch.last_slot_fingerprint = fresh[0][0] if fresh else watch.last_slot_fingerprint
        await watches.flush()
        return WatchRunOutcome(
            outcome="BOOKED" if booked else "OK",
            slots_found=len(result.slots),
            new_slots=len(fresh),
            booked=booked,
        )

    async def _report_missing_locations(
        self, session: AsyncSession, watch: BookingWatch, result: object, *, now: datetime
    ) -> None:
        """Warn once when a saved location is no longer offered by the portal."""
        watches = WatchRepository(session)
        saved = await watches.locations_for(watch.id)
        if not saved:
            return
        available = {location.location_id for location in getattr(result, "locations", ())}
        if not available:
            return
        missing = [item for item in saved if item.portal_location_id not in available]
        if not missing or watch.missing_location_reported_at is not None:
            return
        watch.missing_location_reported_at = now
        await watches.flush()
        await SystemEventRepository(session).record(
            severity=Severity.WARNING,
            component="worker",
            event_code="WATCH_LOCATION_MISSING",
            message="a saved location is no longer returned by the portal",
            context={"watch_id": watch.id.hex, "missing": len(missing)},
            telegram_user_id=watch.telegram_user_id,
        )
        user = await UserRepository(session).get(watch.telegram_user_id)
        if user is not None:
            await self._notifications.queue(
                session,
                user=user,
                event_type=NotificationEventType.LOCATION_DISAPPEARED,
                subject=f"{watch.id.hex}:locations",
                payload={
                    "watch_id": watch.id.hex,
                    "watch_name": watch.name,
                    "missing": [item.location_name for item in missing],
                },
                watch_id=watch.id,
                watch_notifications_enabled=watch.notifications_enabled,
                now=now,
            )

    async def _try_autobook(
        self,
        session: AsyncSession,
        watch: BookingWatch,
        *,
        account: object,
        patient: object,
        selection: object,
        slot_filter: object,
        slot: PortalSlot,
        user_id: uuid.UUID,
        now: datetime,
    ) -> bool:
        """Attempt the auto-booking protocol for the best matching slot."""
        users = UserRepository(session)
        user = await users.get(user_id)
        if user is None:
            return False
        request = BookingRequest(
            account=account,  # type: ignore[arg-type]
            patient=patient,  # type: ignore[arg-type]
            selection=selection,  # type: ignore[arg-type]
            slot_filter=slot_filter,  # type: ignore[arg-type]
            slot=slot,
            user_id=user_id,
            watch_id=watch.id,
        )
        result = await self._booking.book(session, request, now=now)
        if result.duplicate and not result.outcome.succeeded:
            return False
        if result.outcome.succeeded:
            await self._notifications.queue(
                session,
                user=user,
                event_type=NotificationEventType.AUTOBOOK_SUCCESS,
                subject=f"{result.attempt.id.hex}",
                payload={
                    "watch_id": watch.id.hex,
                    "watch_name": watch.name,
                    "appointment_external_id": result.outcome.appointment_external_id or "",
                    "slot": slot.starts_at_local.strftime("%d/%m/%Y %H:%M"),
                    "location": slot.location_name,
                },
                watch_id=watch.id,
                booking_attempt_id=result.attempt.id,
                now=now,
            )
            return True
        event_type = (
            NotificationEventType.AUTOBOOK_AMBIGUOUS
            if result.outcome.ambiguous
            else NotificationEventType.AUTOBOOK_FAILED
        )
        if result.attempt.status is BookingAttemptStatus.SLOT_LOST:
            event_type = NotificationEventType.SLOT_LOST
        await self._notifications.queue(
            session,
            user=user,
            event_type=event_type,
            subject=f"{result.attempt.id.hex}",
            payload={
                "watch_id": watch.id.hex,
                "watch_name": watch.name,
                "detail": result.outcome.detail,
                "slot": slot.starts_at_local.strftime("%d/%m/%Y %H:%M"),
                "location": slot.location_name,
            },
            watch_id=watch.id,
            booking_attempt_id=result.attempt.id,
            now=now,
        )
        return False

    async def _notify_new_slots(
        self,
        session: AsyncSession,
        watch: BookingWatch,
        *,
        user: object,
        candidates: list[tuple[str, PortalSlot]],
        now: datetime,
    ) -> None:
        seen_slots = SeenSlotRepository(session)
        known = await seen_slots.fingerprints_for(watch.id)
        shown = candidates[: watch.max_results]
        # A "notify only changes" monitor deduplicates on the first new slot;
        # a recap monitor deduplicates per run so the summary is not suppressed.
        subject = (
            f"{watch.id.hex}:{shown[0][0]}"
            if watch.notify_only_changes
            else f"{watch.id.hex}:recap:{now.isoformat(timespec='minutes')}"
        )
        await self._notifications.queue(
            session,
            user=user,  # type: ignore[arg-type]
            event_type=NotificationEventType.NEW_SLOT,
            subject=subject,
            payload={
                "watch_id": watch.id.hex,
                "watch_name": watch.name,
                "count": len(shown),
                "slots": [
                    {
                        "fingerprint": fingerprint,
                        "when": slot.starts_at_local.strftime("%d/%m/%Y %H:%M"),
                        "location": slot.location_name,
                        "doctor": slot.doctor_name or "",
                    }
                    for fingerprint, slot in shown
                ],
            },
            watch_id=watch.id,
            watch_notifications_enabled=watch.notifications_enabled,
            now=now,
        )
        for fingerprint, _ in shown:
            record = known.get(fingerprint)
            if record is not None:
                await seen_slots.mark_notified(record, now=now)

    async def _suspend_for_reauth(
        self, session: AsyncSession, watch: BookingWatch, *, error_code: str
    ) -> None:
        watches = WatchRepository(session)
        await watches.set_status(watch, WatchStatus.PAUSED, reason=REASON_REAUTH_REQUIRED)
        users = UserRepository(session)
        user = await users.get(watch.telegram_user_id)
        if user is None:
            return
        account = await PortalAccountRepository(session).get(watch.portal_account_id)
        # The session manager already queues this when it is the one that finds
        # the login gone; sharing its subject keeps the two paths a single
        # message. A disabled account never reaches it, and is reported here.
        subject = (
            reauth_subject(account) if account is not None else f"{watch.portal_account_id.hex}"
        )
        await self._notifications.queue(
            session,
            user=user,
            event_type=NotificationEventType.REAUTH_REQUIRED,
            subject=subject,
            payload={
                "account_id": watch.portal_account_id.hex,
                "account_label": "" if account is None else account.label,
                "status": "" if account is None else account.status.value,
                "watch_name": watch.name,
                "error_code": error_code,
            },
            watch_id=watch.id,
            portal_account_id=watch.portal_account_id,
        )

    async def _suspend_for_ambiguity(
        self, session: AsyncSession, watch: BookingWatch, *, error_code: str
    ) -> None:
        watches = WatchRepository(session)
        await watches.set_status(watch, WatchStatus.PAUSED, reason="AMBIGUOUS_SEARCH")
        users = UserRepository(session)
        user = await users.get(watch.telegram_user_id)
        if user is None:
            return
        await self._notifications.queue(
            session,
            user=user,
            event_type=NotificationEventType.WATCH_AMBIGUOUS_SEARCH,
            subject=f"{watch.id.hex}:ambiguous",
            payload={
                "watch_id": watch.id.hex,
                "watch_name": watch.name,
                "error_code": error_code,
            },
            watch_id=watch.id,
        )

    async def _notify_suspended(
        self, session: AsyncSession, watch: BookingWatch, *, reason: str
    ) -> None:
        user = await UserRepository(session).get(watch.telegram_user_id)
        if user is None:
            return
        await self._notifications.queue(
            session,
            user=user,
            event_type=NotificationEventType.WATCH_SUSPENDED,
            subject=f"{watch.id.hex}:suspended",
            payload={"watch_id": watch.id.hex, "watch_name": watch.name, "reason": reason},
            watch_id=watch.id,
        )


class AccountRecoveryTask:
    """Brings a refused account back without asking the user for anything.

    An account only reaches this task after the portal refused a fresh login,
    so the retry is deliberately unhurried and bounded: the query only offers
    accounts whose last failure is older than the configured delay, and every
    genuine refusal counts towards the lockout that does hand the account to a
    human. A password the user really changed cannot be guessed here, and a
    CAPTCHA is a request for a person by design.
    """

    def __init__(
        self,
        settings: Settings,
        sessions: PortalSessionManager,
        notifications: NotificationService | None = None,
    ) -> None:
        self._settings = settings
        self._sessions = sessions
        self._notifications = notifications

    async def run(
        self, session: AsyncSession, *, now: datetime | None = None, limit: int | None = None
    ) -> int:
        """Try the stored credentials of every account that is due, and report."""
        if not self._settings.account_recovery_enabled:
            return 0
        moment = now or utc_now()
        accounts = PortalAccountRepository(session)
        due = await accounts.list_awaiting_recovery(
            older_than=moment - timedelta(seconds=self._settings.account_recovery_delay_seconds),
            limit=limit or self._settings.account_recovery_batch_size,
        )
        recovered = 0
        for account in due:
            try:
                if not await self._sessions.try_recover(session, account):
                    continue
                resumed = await WatchRepository(session).resume_for_account(
                    account.id, reason=REASON_REAUTH_REQUIRED
                )
            except DomainError as exc:
                # One account's surprise must not discard the whole batch.
                logger.warning(
                    "account recovery failed",
                    extra={
                        "event_code": "ACCOUNT_RECOVERY_FAILED",
                        "account_id": account.id.hex,
                        "error_code": exc.code,
                    },
                )
                continue
            await self._notify_recovered(session, account, resumed=len(resumed), now=moment)
            recovered += 1
        return recovered

    async def _notify_recovered(
        self, session: AsyncSession, account: PortalAccount, *, resumed: int, now: datetime
    ) -> None:
        """Close the loop on the message that reported the account as lost."""
        if self._notifications is None:
            return
        user = await UserRepository(session).get(account.telegram_user_id)
        if user is None:
            return
        await self._notifications.queue(
            session,
            user=user,
            event_type=NotificationEventType.ACCOUNT_RECOVERED,
            subject=reauth_subject(account),
            payload={
                "account_id": account.id.hex,
                "account_label": account.label,
                "resumed": resumed,
            },
            portal_account_id=account.id,
            now=now,
        )


class ReconciliationTask:
    """Recovers booking attempts whose confirmation was never reconciled."""

    def __init__(self, settings: Settings, search: SearchService) -> None:
        self._settings = settings
        self._search = search

    async def run(self, session: AsyncSession, *, now: datetime | None = None) -> int:
        """Mark stale unverified attempts as ambiguous instead of retrying them."""
        moment = now or utc_now()
        attempts = BookingAttemptRepository(session)
        stale = await attempts.list_unreconciled(older_than=moment - timedelta(minutes=5))
        for attempt in stale:
            await attempts.finish(
                attempt,
                status=BookingAttemptStatus.AMBIGUOUS,
                error_code="RECONCILE_TIMEOUT",
                error_detail="worker stopped before the appointment list was reloaded",
            )
            await SystemEventRepository(session).record(
                severity=Severity.ERROR,
                component="worker",
                event_code="BOOKING_RECONCILE_TIMEOUT",
                message="a booking confirmation was never reconciled",
                context={"attempt_id": attempt.id.hex},
                correlation_id=attempt.correlation_id,
            )
        return len(stale)


class AppointmentSyncTask:
    """Periodically refreshes the appointment cache of every active account."""

    def __init__(
        self,
        settings: Settings,
        appointments: object,
        notifications: NotificationService | None = None,
    ) -> None:
        self._settings = settings
        self._appointments = appointments
        self._notifications = notifications

    async def run(self, session: AsyncSession, *, limit: int = 5) -> int:
        accounts = PortalAccountRepository(session)
        threshold = utc_now() - timedelta(seconds=self._settings.appointment_sync_interval_seconds)
        due = await accounts.list_needing_sync(older_than=threshold, limit=limit)
        synchronised = 0
        for account in due:
            try:
                result = await self._appointments.sync_account(  # type: ignore[attr-defined]
                    session, account
                )
            except DomainError as exc:
                await accounts.mark_error(account, error_code=exc.code)
                logger.warning(
                    "appointment synchronisation failed",
                    extra={
                        "event_code": "APPOINTMENT_SYNC_FAILED",
                        "account_id": account.id.hex,
                        "error_code": exc.code,
                    },
                )
                continue
            await self._notify_detected(session, account, result)
            synchronised += 1
        return synchronised

    async def _notify_detected(
        self, session: AsyncSession, account: object, result: object
    ) -> None:
        """Tell the owner about appointments that appeared without their action."""
        detected = getattr(result, "detected", ())
        if not detected or self._notifications is None:
            return
        owner_id = getattr(account, "telegram_user_id", None)
        if owner_id is None:
            return
        user = await UserRepository(session).get(owner_id)
        if user is None:
            return
        appointments = AppointmentRepository(session)
        for appointment_id in detected:
            record = await appointments.get_any(appointment_id)
            if record is None:
                continue
            await self._notifications.queue(
                session,
                user=user,
                event_type=NotificationEventType.APPOINTMENT_DETECTED,
                subject=f"{record.id.hex}:detected",
                payload={
                    "when": record.scheduled_at_utc.isoformat() if record.scheduled_at_utc else "",
                    "location": record.location_name or "",
                },
                appointment_id=record.id,
                portal_account_id=record.portal_account_id,
            )


class CleanupTask:
    """Applies the retention policy to short lived and historical records."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def run(self, session: AsyncSession, *, now: datetime | None = None) -> dict[str, int]:
        moment = now or utc_now()
        return {
            "confirmation_nonces": await ConfirmationNonceRepository(session).purge_expired(
                before=moment
            ),
            "conversation_states": await ConversationStateRepository(session).purge_expired(
                before=moment
            ),
            "booking_drafts": await BookingDraftRepository(session).purge_expired(before=moment),
            "callbacks": await CallbackConsumptionRepository(session).purge_expired(before=moment),
            "rate_limits": await ConnectRateLimitRepository(session).purge_before(
                before=moment - timedelta(days=2)
            ),
            "seen_slots": await SeenSlotRepository(session).purge_old(
                before=moment - timedelta(days=self._settings.seen_slot_retention_days)
            ),
            "watch_runs": await WatchRunLogRepository(session).purge_old(
                before=moment - timedelta(days=self._settings.event_retention_days)
            ),
            "notifications": await NotificationRepository(session).purge_old(
                before=moment - timedelta(days=self._settings.notification_retention_days)
            ),
            "system_events": await SystemEventRepository(session).purge_old(
                before=moment - timedelta(days=self._settings.event_retention_days)
            ),
            "portal_calls": await PortalCallLogRepository(session).purge_old(
                before=moment - timedelta(hours=6)
            ),
            "appointments": await AppointmentRepository(session).purge_old(
                before=moment - timedelta(days=self._settings.appointment_retention_days)
            ),
            "invites": await InviteRepository(session).purge_expired(
                older_than=moment - timedelta(days=self._settings.event_retention_days)
            ),
            "heartbeats": await HeartbeatRepository(session).purge_stale(
                before=moment - timedelta(days=7)
            ),
            # The audit trail is append-only for the application; retention is
            # the one deliberate exception, driven by AUDIT_RETENTION_DAYS.
            "audit_entries": await AuditRepository(session).purge_before(
                before=moment - timedelta(days=self._settings.audit_retention_days)
            ),
        }
