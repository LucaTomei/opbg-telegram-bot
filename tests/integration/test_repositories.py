"""Repository behaviour against a real MySQL server."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.accounts import PortalAccount
from app.db.models.users import TelegramUser
from app.db.repositories.accounts import (
    ConnectRateLimitRepository,
    ConnectTicketRepository,
    DownloadTokenRepository,
    PatientRepository,
    PortalAccountRepository,
)
from app.db.repositories.base import utc_now
from app.db.repositories.bookings import AppointmentRepository, BookingAttemptRepository
from app.db.repositories.conversations import (
    BookingDraftRepository,
    CallbackConsumptionRepository,
    ConfirmationNonceRepository,
    ConversationStateRepository,
)
from app.db.repositories.notifications import NotificationRepository
from app.db.repositories.users import InviteRepository, UserRepository
from app.domain.entities import PortalAppointment
from app.domain.enums import (
    BookingAttemptStatus,
    ConfirmationAction,
    ConnectTicketPurpose,
    NotificationEventType,
    PortalAccountStatus,
    UserRole,
    UserStatus,
)
from app.domain.errors import ConflictError
from app.security.tokens import generate_invite_code, generate_nonce, generate_token, hash_context

SYNTHETIC_EMAIL = "primo.utente@example.invalid"
SYNTHETIC_PASSWORD = "password-sintetica-di-test"
SYNTHETIC_TAX_CODE = "AAABBB01A01A001A"


async def make_account(
    session: AsyncSession, user: TelegramUser, *, email: str = SYNTHETIC_EMAIL
) -> PortalAccount:
    return await PortalAccountRepository(session).create(
        user_id=user.id,
        label="Account di test",
        email=email,
        password=SYNTHETIC_PASSWORD,
        encryption_key_version=1,
    )


# --- users -------------------------------------------------------------------
async def test_user_lookup_by_telegram_id(clean_session: AsyncSession) -> None:
    users = UserRepository(clean_session)
    created = await users.create(telegram_id=999001)
    found = await users.get_by_telegram_id(999001)
    assert found is not None
    assert found.id == created.id


async def test_admin_bootstrap_promotes_and_demotes(clean_session: AsyncSession) -> None:
    users = UserRepository(clean_session)
    plain = await users.create(telegram_id=999002)
    promoted = await users.upsert_admin(999002, timezone="Europe/Rome")
    assert promoted.id == plain.id
    assert promoted.role is UserRole.ADMIN

    await users.upsert_admin(999003, timezone="Europe/Rome")
    demoted = await users.demote_admins_except([999003])
    assert demoted == 1
    refreshed = await users.get_by_telegram_id(999002)
    assert refreshed is not None
    assert refreshed.role is UserRole.USER


async def test_encrypted_profile_round_trips(clean_session: AsyncSession) -> None:
    users = UserRepository(clean_session)
    user = await users.create(telegram_id=999004)
    user.username = "utente_sintetico"
    user.first_name = "Nome"
    await users.flush()
    clean_session.expunge_all()

    reloaded = await UserRepository(clean_session).get_by_telegram_id(999004)
    assert reloaded is not None
    assert reloaded.username == "utente_sintetico"
    assert reloaded.first_name == "Nome"
    assert reloaded.username_ciphertext is not None
    assert b"utente_sintetico" not in reloaded.username_ciphertext


async def test_user_status_counts(clean_session: AsyncSession, user: TelegramUser) -> None:
    counts = await UserRepository(clean_session).count_by_status()
    assert counts[UserStatus.ACTIVE] >= 1


# --- invites -----------------------------------------------------------------
async def test_invite_lifecycle(clean_session: AsyncSession, admin_user: TelegramUser) -> None:
    invites = InviteRepository(clean_session)
    code = generate_invite_code()
    invite = await invites.create(
        created_by_user_id=admin_user.id, code=code, ttl_hours=24, max_uses=2
    )
    assert invite.is_usable(utc_now()) is True

    found = await invites.get_by_code(code.lower())
    assert found is not None and found.id == invite.id
    assert await invites.get_by_code("ZZZZ-ZZZZ-ZZZZ") is None

    await invites.redeem(invite, admin_user, utc_now())
    assert invite.used_count == 1
    assert await invites.already_redeemed(invite, admin_user) is True

    await invites.revoke(invite)
    assert invite.is_usable(utc_now()) is False


async def test_invite_cannot_be_redeemed_twice_by_the_same_user(
    clean_session: AsyncSession, admin_user: TelegramUser
) -> None:
    invites = InviteRepository(clean_session)
    invite = await invites.create(
        created_by_user_id=admin_user.id, code=generate_invite_code(), ttl_hours=24, max_uses=5
    )
    await invites.redeem(invite, admin_user, utc_now())
    with pytest.raises(ConflictError):
        await invites.redeem(invite, admin_user, utc_now())


# --- portal accounts ---------------------------------------------------------
async def test_account_credentials_are_encrypted_at_rest(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    account = await make_account(clean_session, user)
    clean_session.expunge_all()
    reloaded = await PortalAccountRepository(clean_session).get(account.id)
    assert reloaded is not None
    assert reloaded.email == SYNTHETIC_EMAIL
    assert reloaded.password == SYNTHETIC_PASSWORD
    assert reloaded.password_ciphertext is not None
    assert SYNTHETIC_PASSWORD.encode() not in reloaded.password_ciphertext
    assert SYNTHETIC_EMAIL.encode() not in (reloaded.email_ciphertext or b"")


async def test_account_lookup_uses_the_blind_index(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    await make_account(clean_session, user)
    accounts = PortalAccountRepository(clean_session)
    assert await accounts.find_by_email(user.id, SYNTHETIC_EMAIL.upper()) is not None
    assert await accounts.find_by_email(user.id, "altro@example.invalid") is None


async def test_the_same_account_cannot_be_connected_twice(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    await make_account(clean_session, user)
    with pytest.raises(ConflictError):
        await make_account(clean_session, user)


async def test_two_users_may_connect_the_same_portal_account(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    other = await UserRepository(clean_session).create(
        telegram_id=999010, status=UserStatus.ACTIVE
    )
    await make_account(clean_session, user)
    await make_account(clean_session, other)


async def test_soft_deleted_account_frees_the_unique_constraint(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    accounts = PortalAccountRepository(clean_session)
    first = await make_account(clean_session, user)
    await accounts.soft_delete(first)
    assert first.password is None
    assert first.cookie_jar is None
    second = await make_account(clean_session, user)
    assert second.id != first.id
    assert len(await accounts.list_for_user(user.id)) == 1


async def test_account_ownership_is_enforced(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    account = await make_account(clean_session, user)
    other = await UserRepository(clean_session).create(telegram_id=999011)
    accounts = PortalAccountRepository(clean_session)
    assert await accounts.get_owned(account.id, user.id) is not None
    assert await accounts.get_owned(account.id, other.id) is None


async def test_authentication_failures_lock_the_account(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    accounts = PortalAccountRepository(clean_session)
    account = await make_account(clean_session, user)
    await accounts.mark_auth_failure(account, error_code="AUTH", max_failures=2)
    assert account.status is PortalAccountStatus.REAUTH_REQUIRED
    await accounts.mark_auth_failure(account, error_code="AUTH", max_failures=2)
    assert account.status is PortalAccountStatus.LOCKED
    assert account.cookie_jar is None


async def test_storing_a_session_clears_the_failure_counter(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    accounts = PortalAccountRepository(clean_session)
    account = await make_account(clean_session, user)
    await accounts.mark_auth_failure(account, error_code="AUTH", max_failures=5)
    await accounts.store_session(
        account, cookies={"opbg-prenotazione": "synthetic"}, metadata={"id": 1}
    )
    assert account.consecutive_auth_failures == 0
    assert account.status is PortalAccountStatus.ACTIVE
    assert account.cookie_jar == {"opbg-prenotazione": "synthetic"}


# --- patients ----------------------------------------------------------------
async def test_patients_are_upserted_and_deactivated(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    account = await make_account(clean_session, user)
    patients = PatientRepository(clean_session)
    now = utc_now()
    first = await patients.upsert(
        account_id=account.id,
        tax_code=SYNTHETIC_TAX_CODE,
        display_name="Alfa Beta",
        relation="figlio",
        now=now,
    )
    again = await patients.upsert(
        account_id=account.id,
        tax_code=SYNTHETIC_TAX_CODE,
        display_name="Alfa Beta Aggiornato",
        relation="figlio",
        now=now,
    )
    assert first.id == again.id
    assert again.display_name == "Alfa Beta Aggiornato"

    await patients.upsert(
        account_id=account.id,
        tax_code="CCCDDD02B02B002B",
        display_name="Gamma Delta",
        relation=None,
        now=now,
    )
    deactivated = await patients.deactivate_missing(
        account.id, seen_tax_codes=[SYNTHETIC_TAX_CODE]
    )
    assert deactivated == 1
    assert len(await patients.list_for_account(account.id)) == 1


async def test_patient_tax_code_is_encrypted(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    account = await make_account(clean_session, user)
    patients = PatientRepository(clean_session)
    patient = await patients.upsert(
        account_id=account.id,
        tax_code=SYNTHETIC_TAX_CODE,
        display_name="Alfa Beta",
        relation=None,
        now=utc_now(),
    )
    clean_session.expunge_all()
    reloaded = await PatientRepository(clean_session).get(patient.id)
    assert reloaded is not None
    assert reloaded.external_tax_code == SYNTHETIC_TAX_CODE
    assert SYNTHETIC_TAX_CODE.encode() not in reloaded.external_tax_code_ciphertext


# --- tickets and tokens ------------------------------------------------------
async def test_connect_ticket_is_single_use(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    tickets = ConnectTicketRepository(clean_session)
    token = generate_token()
    ticket = await tickets.create(
        user_id=user.id, token=token, purpose=ConnectTicketPurpose.CONNECT, ttl_minutes=10
    )
    assert ticket.is_usable(utc_now()) is True
    found = await tickets.get_by_token(token)
    assert found is not None
    await tickets.consume(found)
    assert found.is_usable(utc_now()) is False
    assert await tickets.get_by_token("altro-token") is None


async def test_new_ticket_invalidates_the_previous_ones(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    tickets = ConnectTicketRepository(clean_session)
    await tickets.create(
        user_id=user.id,
        token=generate_token(),
        purpose=ConnectTicketPurpose.CONNECT,
        ttl_minutes=10,
    )
    assert await tickets.invalidate_for_user(user.id) == 1


async def test_download_token_is_single_use(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    account = await make_account(clean_session, user)
    appointment, _ = await AppointmentRepository(clean_session).upsert(
        user_id=user.id,
        account_id=account.id,
        patient_id=None,
        appointment=PortalAppointment(
            external_id="70001",
            service_name="VISITA SINTETICA",
            doctor_name=None,
            patient_label="Alfa Beta",
            location_name="Sede Alfa",
            scheduled_at_local=None,
            scheduled_at_utc=datetime(2026, 9, 15, 7, 30, tzinfo=UTC),
            status_hint="pending",
            payment_note=None,
            cancel_path="/appointments/70001/cancel",
            pdf_path="/appointments/70001/pdf-download",
            raw_fingerprint="fp",
        ),
        now=utc_now(),
    )
    tokens = DownloadTokenRepository(clean_session)
    token = generate_token()
    record = await tokens.create(
        user_id=user.id, appointment_id=appointment.id, token=token, ttl_minutes=5
    )
    assert record.is_usable(utc_now()) is True
    await tokens.consume(record)
    assert record.is_usable(utc_now()) is False


async def test_connect_rate_limit_counts_per_window(clean_session: AsyncSession) -> None:
    limits = ConnectRateLimitRepository(clean_session)
    window = utc_now().replace(minute=0, second=0, microsecond=0)
    subject = b"\x01" * 32
    results = [
        await limits.hit(scope="ip", subject_hash=subject, window_start=window, limit=2)
        for _ in range(3)
    ]
    assert results == [False, False, True]


# --- conversation state ------------------------------------------------------
async def test_conversation_state_survives_a_restart(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    states = ConversationStateRepository(clean_session)
    await states.set_state(
        user.id, state="booking:waiting_query", payload={"draft": "abc"}, ttl_seconds=600
    )
    clean_session.expunge_all()

    reloaded = await ConversationStateRepository(clean_session).get_for_user(user.id)
    assert reloaded is not None
    assert reloaded.state == "booking:waiting_query"
    assert reloaded.payload == {"draft": "abc"}
    assert reloaded.payload_ciphertext is not None
    assert b"abc" not in reloaded.payload_ciphertext


async def test_expired_conversation_state_is_discarded(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    states = ConversationStateRepository(clean_session)
    record = await states.set_state(user.id, state="x", payload=None, ttl_seconds=600)
    record.expires_at = utc_now() - timedelta(seconds=1)
    await states.flush()
    assert await states.get_for_user(user.id) is None


# --- confirmation nonces -----------------------------------------------------
async def test_confirmation_nonce_is_single_use(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    nonces = ConfirmationNonceRepository(clean_session)
    nonce = generate_nonce()
    record = await nonces.create(
        user_id=user.id,
        action=ConfirmationAction.CANCEL_APPOINTMENT,
        target_type="appointment",
        target_id="abc",
        nonce=nonce,
        context_hash=hash_context("abc"),
        ttl_seconds=120,
    )
    assert await nonces.consume(record) is True
    assert await nonces.consume(record) is False


async def test_issuing_a_new_nonce_invalidates_the_previous_one(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    nonces = ConfirmationNonceRepository(clean_session)
    await nonces.create(
        user_id=user.id,
        action=ConfirmationAction.DELETE_WATCH,
        target_type="watch",
        target_id="abc",
        nonce=generate_nonce(),
        context_hash=hash_context("abc"),
        ttl_seconds=120,
    )
    invalidated = await nonces.invalidate_for_target(
        user_id=user.id, action=ConfirmationAction.DELETE_WATCH, target_id="abc"
    )
    assert invalidated == 1


# --- callback idempotency ----------------------------------------------------
async def test_callback_claim_is_idempotent(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    callbacks = CallbackConsumptionRepository(clean_session)
    key = b"\x02" * 32
    assert await callbacks.claim(dedup_key=key, user_id=user.id, ttl_seconds=60) is True
    assert await callbacks.claim(dedup_key=key, user_id=user.id, ttl_seconds=60) is False


# --- booking attempts --------------------------------------------------------
async def test_booking_attempt_is_created_once_per_idempotency_key(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    account = await make_account(clean_session, user)
    attempts = BookingAttemptRepository(clean_session)
    first, created_first = await attempts.start(
        user_id=user.id,
        account_id=account.id,
        patient_id=None,
        idempotency_key="key-1",
        slot_fingerprint="fp-1",
    )
    second, created_second = await attempts.start(
        user_id=user.id,
        account_id=account.id,
        patient_id=None,
        idempotency_key="key-1",
        slot_fingerprint="fp-1",
    )
    assert created_first is True
    assert created_second is False
    assert first.id == second.id


async def test_a_watch_cannot_book_the_same_slot_twice(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    account = await make_account(clean_session, user)
    attempts = BookingAttemptRepository(clean_session)
    watch_id = uuid.uuid4()
    success_key = f"{watch_id.hex}:fp-1"

    first, _ = await attempts.start(
        user_id=user.id,
        account_id=account.id,
        patient_id=None,
        idempotency_key="key-a",
        slot_fingerprint="fp-1",
    )
    await attempts.finish(
        first,
        status=BookingAttemptStatus.SUCCEEDED,
        appointment_external_id="70001",
        watch_slot_success_key=success_key,
    )
    assert await attempts.has_successful_for_watch_slot(success_key) is True

    second, _ = await attempts.start(
        user_id=user.id,
        account_id=account.id,
        patient_id=None,
        idempotency_key="key-b",
        slot_fingerprint="fp-1",
    )
    with pytest.raises(ConflictError):
        await attempts.finish(
            second,
            status=BookingAttemptStatus.SUCCEEDED,
            appointment_external_id="70002",
            watch_slot_success_key=success_key,
        )


async def test_unreconciled_attempts_are_listed(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    account = await make_account(clean_session, user)
    attempts = BookingAttemptRepository(clean_session)
    attempt, _ = await attempts.start(
        user_id=user.id,
        account_id=account.id,
        patient_id=None,
        idempotency_key="key-c",
        slot_fingerprint="fp-2",
    )
    await attempts.mark_confirm_sent(attempt)
    attempt.confirm_sent_at = utc_now() - timedelta(minutes=30)
    await attempts.flush()
    stale = await attempts.list_unreconciled(older_than=utc_now() - timedelta(minutes=5))
    assert [item.id for item in stale] == [attempt.id]


# --- notifications -----------------------------------------------------------
async def test_notifications_are_deduplicated(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    notifications = NotificationRepository(clean_session)
    key = b"\x03" * 32
    first = await notifications.enqueue(
        user_id=user.id,
        event_type=NotificationEventType.NEW_SLOT,
        dedup_key=key,
        payload={"count": 1},
    )
    second = await notifications.enqueue(
        user_id=user.id,
        event_type=NotificationEventType.NEW_SLOT,
        dedup_key=key,
        payload={"count": 2},
    )
    assert first is not None
    assert second is None


async def test_notification_payload_is_encrypted(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    notifications = NotificationRepository(clean_session)
    event = await notifications.enqueue(
        user_id=user.id,
        event_type=NotificationEventType.NEW_SLOT,
        dedup_key=b"\x04" * 32,
        payload={"location": "Sede Alfa"},
    )
    assert event is not None
    clean_session.expunge_all()
    reloaded = await NotificationRepository(clean_session).get(event.id)
    assert reloaded is not None
    assert reloaded.payload == {"location": "Sede Alfa"}
    assert b"Sede Alfa" not in (reloaded.payload_ciphertext or b"")


# --- drafts ------------------------------------------------------------------
async def test_open_drafts_are_abandoned_on_cancel(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    drafts = BookingDraftRepository(clean_session)
    await drafts.create(user_id=user.id, ttl_seconds=600)
    assert await drafts.abandon_open_for_user(user.id) == 1
    assert await drafts.latest_open(user.id) is None


async def test_draft_is_owned_by_its_user(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    drafts = BookingDraftRepository(clean_session)
    draft = await drafts.create(user_id=user.id, ttl_seconds=600)
    other = await UserRepository(clean_session).create(telegram_id=999020)
    assert await drafts.get_owned(draft.id, user.id) is not None
    assert await drafts.get_owned(draft.id, other.id) is None
