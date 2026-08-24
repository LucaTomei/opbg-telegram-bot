"""Worker execution: monitors, notifications, retention and recovery."""

from __future__ import annotations

import random
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.composition import Container
from app.db.models.users import TelegramUser
from app.db.repositories.base import utc_now
from app.db.repositories.bookings import BookingAttemptRepository
from app.db.repositories.notifications import NotificationRepository
from app.db.repositories.system import SystemEventRepository
from app.db.repositories.watches import SeenSlotRepository, WatchRepository
from app.db.session import Database
from app.domain.enums import (
    BookingAttemptStatus,
    NotificationEventType,
    NotificationStatus,
    PortalAccountStatus,
    UserStatus,
    WatchMode,
    WatchStatus,
)
from app.worker.tasks import (
    AppointmentSyncTask,
    CleanupTask,
    ReconciliationTask,
    WatchProcessor,
)
from tests.support.factories import make_account, make_patient, make_watch
from tests.support.portal import FakePortal, RecordingSender

WORKER_ID = "worker-test"


def build_processor(container: Container) -> WatchProcessor:
    return WatchProcessor(
        container.settings,
        container.search,
        container.bookings,
        container.notifications,
        jitter=random.Random(1),
    )


async def test_notify_only_monitor_queues_new_slots(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    account = await make_account(clean_session, user)
    patient = await make_patient(clean_session, account)
    watch = await make_watch(clean_session, user, account, patient=patient)

    outcome = await build_processor(container).run(clean_session, watch, worker_id=WORKER_ID)
    assert outcome.outcome == "OK"
    assert outcome.slots_found == 3
    assert outcome.new_slots == 3

    events = await NotificationRepository(clean_session).list_for_user(
        user.id, page=0, page_size=10
    )
    assert events.total == 1
    assert events.items[0].event_type is NotificationEventType.NEW_SLOT


async def test_only_new_slots_are_notified_on_the_second_run(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    account = await make_account(clean_session, user)
    patient = await make_patient(clean_session, account)
    watch = await make_watch(clean_session, user, account, patient=patient)
    processor = build_processor(container)

    await processor.run(clean_session, watch, worker_id=WORKER_ID)
    watch.next_check_at = utc_now()
    second = await processor.run(clean_session, watch, worker_id=WORKER_ID)
    assert second.new_slots == 0
    events = await NotificationRepository(clean_session).list_for_user(
        user.id, page=0, page_size=10
    )
    assert events.total == 1


async def test_seen_slots_are_recorded_and_marked_absent(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    portal: FakePortal,
) -> None:
    account = await make_account(clean_session, user)
    patient = await make_patient(clean_session, account)
    watch = await make_watch(clean_session, user, account, patient=patient)
    processor = build_processor(container)
    await processor.run(clean_session, watch, worker_id=WORKER_ID)

    seen = SeenSlotRepository(clean_session)
    assert len(await seen.fingerprints_for(watch.id)) == 3

    portal.availability_fixture = "availability_empty.json"
    await processor.run(clean_session, watch, worker_id=WORKER_ID)
    records = await seen.fingerprints_for(watch.id)
    assert all(record.currently_available is False for record in records.values())


async def test_recap_mode_notifies_on_every_run(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    account = await make_account(clean_session, user)
    patient = await make_patient(clean_session, account)
    watch = await make_watch(
        clean_session, user, account, patient=patient, notify_only_changes=False
    )
    processor = build_processor(container)
    await processor.run(clean_session, watch, worker_id=WORKER_ID)
    await processor.run(clean_session, watch, worker_id=WORKER_ID)
    events = await NotificationRepository(clean_session).list_for_user(
        user.id, page=0, page_size=10
    )
    assert events.total >= 1


async def test_notifications_disabled_on_the_watch_suppresses_the_event(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    account = await make_account(clean_session, user)
    patient = await make_patient(clean_session, account)
    watch = await make_watch(
        clean_session, user, account, patient=patient, notifications_enabled=False
    )
    await build_processor(container).run(clean_session, watch, worker_id=WORKER_ID)
    events = await NotificationRepository(clean_session).list_for_user(
        user.id, page=0, page_size=10
    )
    assert events.items[0].status is NotificationStatus.SUPPRESSED


async def test_autobook_books_the_best_slot_and_completes_the_monitor(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    portal: FakePortal,
) -> None:
    account = await make_account(clean_session, user)
    patient = await make_patient(clean_session, account)
    watch = await make_watch(clean_session, user, account, patient=patient, mode=WatchMode.AUTOBOOK)
    portal.appointments_fixture = "appointments_list_empty.html"
    portal.appointments_after_booking = "appointments_list.html"

    outcome = await build_processor(container).run(clean_session, watch, worker_id=WORKER_ID)
    assert outcome.booked is True
    assert portal.confirmations == 1
    assert watch.status is WatchStatus.COMPLETED

    events = await NotificationRepository(clean_session).list_for_user(
        user.id, page=0, page_size=10
    )
    assert events.items[0].event_type is NotificationEventType.AUTOBOOK_SUCCESS


async def test_autobook_never_books_a_slot_outside_the_filters(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    portal: FakePortal,
) -> None:
    account = await make_account(clean_session, user)
    patient = await make_patient(clean_session, account)
    watch = await make_watch(clean_session, user, account, patient=patient, mode=WatchMode.AUTOBOOK)
    # Only Sundays are accepted; the synthetic slots are on other weekdays.
    watch.weekdays_mask = 0b1000000
    await WatchRepository(clean_session).flush()

    outcome = await build_processor(container).run(clean_session, watch, worker_id=WORKER_ID)
    assert outcome.booked is False
    assert portal.confirmations == 0


async def test_ambiguous_autobook_is_notified_and_not_retried(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    portal: FakePortal,
) -> None:
    account = await make_account(clean_session, user)
    patient = await make_patient(clean_session, account)
    watch = await make_watch(clean_session, user, account, patient=patient, mode=WatchMode.AUTOBOOK)
    portal.booking_is_ambiguous = True
    outcome = await build_processor(container).run(clean_session, watch, worker_id=WORKER_ID)
    assert outcome.booked is False
    assert portal.confirmations == 1
    events = await NotificationRepository(clean_session).list_for_user(
        user.id, page=0, page_size=10
    )
    assert events.items[0].event_type is NotificationEventType.AUTOBOOK_AMBIGUOUS


async def test_authentication_failure_suspends_only_that_account(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    portal: FakePortal,
) -> None:
    first_account = await make_account(clean_session, user)
    second_account = await make_account(
        clean_session, user, email="secondo@example.invalid", label="Secondo"
    )
    first_patient = await make_patient(clean_session, first_account)
    second_patient = await make_patient(clean_session, second_account)
    failing = await make_watch(clean_session, user, first_account, patient=first_patient)
    healthy = await make_watch(clean_session, user, second_account, patient=second_patient)

    processor = build_processor(container)
    portal.session_expired = True
    portal.valid_credentials = ("nessuno@example.invalid", "x" * 12)
    await processor.run(clean_session, failing, worker_id=WORKER_ID)
    assert failing.status is WatchStatus.PAUSED
    assert healthy.status is WatchStatus.ACTIVE

    events = await NotificationRepository(clean_session).list_for_user(
        user.id, page=0, page_size=10
    )
    assert events.items[0].event_type is NotificationEventType.REAUTH_REQUIRED


async def test_a_suspended_user_stops_the_monitor(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    account = await make_account(clean_session, user)
    patient = await make_patient(clean_session, account)
    watch = await make_watch(clean_session, user, account, patient=patient)
    user.status = UserStatus.SUSPENDED
    await WatchRepository(clean_session).flush()
    outcome = await build_processor(container).run(clean_session, watch, worker_id=WORKER_ID)
    assert outcome.outcome == "USER_NOT_ACTIVE"
    assert watch.status is WatchStatus.PAUSED


async def test_a_disabled_account_stops_the_monitor(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    account = await make_account(clean_session, user)
    patient = await make_patient(clean_session, account)
    watch = await make_watch(clean_session, user, account, patient=patient)
    account.status = PortalAccountStatus.DISABLED
    await WatchRepository(clean_session).flush()
    outcome = await build_processor(container).run(clean_session, watch, worker_id=WORKER_ID)
    assert outcome.outcome == "ACCOUNT_NOT_ACTIVE"


async def test_repeated_portal_errors_suspend_the_monitor(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    portal: FakePortal,
    database: Database,
) -> None:
    """Backoff grows and the monitor is suspended once the threshold is hit."""
    from tests.support.portal import build_container, make_settings

    # The breaker is kept closed so the per-monitor error policy is exercised.
    tolerant = build_container(
        make_settings(
            container.settings,
            breaker_account_failure_threshold=50,
            breaker_global_failure_threshold=50,
            monitor_max_consecutive_errors=3,
        ),
        database,
        portal,
    )
    account = await make_account(clean_session, user)
    patient = await make_patient(clean_session, account)
    watch = await make_watch(clean_session, user, account, patient=patient)
    portal.forced_status = 500
    processor = build_processor(tolerant)

    delays: list[float] = []
    for _ in range(3):
        before = utc_now()
        outcome = await processor.run(clean_session, watch, worker_id=WORKER_ID)
        assert outcome.outcome == "ERROR"
        if watch.next_check_at is not None:
            delays.append((watch.next_check_at - before).total_seconds())
    assert watch.status is WatchStatus.ERROR
    assert watch.next_check_at is None
    assert delays == sorted(delays)

    events = await SystemEventRepository(clean_session).list_events(page=0, page_size=50)
    assert events.total >= 1


async def test_the_circuit_breaker_defers_instead_of_failing(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    portal: FakePortal,
) -> None:
    """Once the breaker opens, monitors are deferred to protect the portal."""
    account = await make_account(clean_session, user)
    patient = await make_patient(clean_session, account)
    watch = await make_watch(clean_session, user, account, patient=patient)
    portal.forced_status = 500
    processor = build_processor(container)

    outcomes = [
        (await processor.run(clean_session, watch, worker_id=WORKER_ID)).outcome
        for _ in range(container.settings.breaker_account_failure_threshold + 2)
    ]
    assert "DEFERRED" in outcomes
    assert watch.status is WatchStatus.ACTIVE


async def test_transient_failures_do_not_break_the_run(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    portal: FakePortal,
) -> None:
    account = await make_account(clean_session, user)
    patient = await make_patient(clean_session, account)
    watch = await make_watch(clean_session, user, account, patient=patient)
    portal.transient_failures = 2
    outcome = await build_processor(container).run(clean_session, watch, worker_id=WORKER_ID)
    assert outcome.outcome == "OK"


# --- reconciliation and retention -------------------------------------------
async def test_a_worker_crash_between_confirm_and_reconcile_is_recovered(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    account = await make_account(clean_session, user)
    attempts = BookingAttemptRepository(clean_session)
    attempt, _ = await attempts.start(
        user_id=user.id,
        account_id=account.id,
        patient_id=None,
        idempotency_key="crash-1",
        slot_fingerprint="fp",
    )
    await attempts.mark_confirm_sent(attempt)
    attempt.confirm_sent_at = utc_now() - timedelta(minutes=30)
    await attempts.flush()

    recovered = await ReconciliationTask(container.settings, container.search).run(clean_session)
    assert recovered == 1
    assert attempt.status is BookingAttemptStatus.AMBIGUOUS


async def test_appointment_sync_task_refreshes_due_accounts(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    account = await make_account(clean_session, user)
    account.last_sync_at = None
    task = AppointmentSyncTask(container.settings, container.appointments)
    assert await task.run(clean_session) == 1
    assert account.last_sync_at is not None


async def test_cleanup_removes_expired_records(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    from app.db.repositories.accounts import ConnectTicketRepository
    from app.domain.enums import ConnectTicketPurpose
    from app.security.tokens import generate_token

    tickets = ConnectTicketRepository(clean_session)
    ticket = await tickets.create(
        user_id=user.id,
        token=generate_token(),
        purpose=ConnectTicketPurpose.CONNECT,
        ttl_minutes=1,
    )
    ticket.expires_at = utc_now() - timedelta(days=1)
    await tickets.flush()

    removed = await CleanupTask(container.settings).run(clean_session)
    assert removed["connect_tickets"] >= 1


# --- notification delivery ---------------------------------------------------
async def test_pending_notifications_are_delivered(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    from app.bot.notifications import render_for_queue

    account = await make_account(clean_session, user)
    patient = await make_patient(clean_session, account)
    watch = await make_watch(clean_session, user, account, patient=patient)
    await build_processor(container).run(clean_session, watch, worker_id=WORKER_ID)

    sender = RecordingSender()
    sent = await container.notifications.dispatch_pending(clean_session, sender, render_for_queue)
    assert sent == 1
    assert "Nuove disponibilità" in sender.sent[0][1]


async def test_delivery_failures_are_retried_with_backoff(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    from app.bot.notifications import render_for_queue

    account = await make_account(clean_session, user)
    patient = await make_patient(clean_session, account)
    watch = await make_watch(clean_session, user, account, patient=patient)
    await build_processor(container).run(clean_session, watch, worker_id=WORKER_ID)

    sender = RecordingSender()
    sender.failures = 1
    assert (
        await container.notifications.dispatch_pending(clean_session, sender, render_for_queue) == 0
    )
    events = await NotificationRepository(clean_session).list_for_user(
        user.id, page=0, page_size=10
    )
    event = events.items[0]
    assert event.attempt_count == 1
    assert event.status is NotificationStatus.PENDING
    assert event.next_attempt_at is not None
