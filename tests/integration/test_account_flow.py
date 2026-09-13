"""Account connection, re-authentication and patient synchronisation."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.composition import Container
from app.db.models.accounts import PortalAccount
from app.db.models.users import TelegramUser
from app.db.repositories.accounts import PatientRepository, PortalAccountRepository
from app.db.repositories.base import utc_now
from app.db.repositories.notifications import NotificationRepository
from app.db.repositories.users import UserRepository
from app.db.repositories.watches import WatchRepository
from app.db.session import Database
from app.domain.enums import (
    NotificationEventType,
    PortalAccountStatus,
    UserStatus,
    WatchStatus,
)
from app.domain.errors import (
    AuthenticationFailed,
    ConflictError,
    ConsentRequiredError,
    QuotaExceededError,
    RateLimitedError,
    ValidationError,
)
from app.services.portal_sessions import MAX_AUTH_FAILURES
from app.worker.tasks import AccountRecoveryTask, AppointmentSyncTask
from tests.support.factories import make_account, make_watch
from tests.support.portal import VALID_EMAIL, VALID_PASSWORD, FakePortal

OTHER_EMAIL = "secondo.utente@example.invalid"


async def connect_account(
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    *,
    email: str = VALID_EMAIL,
    label: str = "Account principale",
) -> PortalAccount:
    await container.accounts.ensure_can_connect(session, user)
    return await container.accounts.connect(
        session, user, label=label, email=email, password=VALID_PASSWORD
    )


# --- connection --------------------------------------------------------------
async def test_connection_stores_encrypted_credentials_and_patients(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    account = await connect_account(clean_session, container, user)
    assert account.status is PortalAccountStatus.ACTIVE
    assert account.email == VALID_EMAIL
    assert account.cookie_jar
    patients = await PatientRepository(clean_session).list_for_account(account.id)
    assert {patient.external_tax_code for patient in patients} == {
        "AAABBB01A01A001A",
        "CCCDDD02B02B002B",
    }


async def test_the_password_is_never_stored_in_clear(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    account = await connect_account(clean_session, container, user)
    assert account.password_ciphertext is not None
    assert VALID_PASSWORD.encode() not in account.password_ciphertext
    assert account.password == VALID_PASSWORD


async def test_wrong_credentials_do_not_create_an_account(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    with pytest.raises(AuthenticationFailed):
        await container.accounts.connect(
            clean_session,
            user,
            label="Account",
            email=VALID_EMAIL,
            password="password-sbagliata",
        )
    assert await PortalAccountRepository(clean_session).count_for_user(user.id) == 0


async def test_the_same_account_cannot_be_connected_twice(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await connect_account(clean_session, container, user)
    with pytest.raises(ConflictError):
        await container.accounts.connect(
            clean_session,
            user,
            label="Duplicato",
            email=VALID_EMAIL.upper(),
            password=VALID_PASSWORD,
        )


@pytest.mark.parametrize("email", ["senza-chiocciola", "con spazio@example.invalid", ""])
async def test_a_malformed_email_is_refused(
    clean_session: AsyncSession, container: Container, user: TelegramUser, email: str
) -> None:
    with pytest.raises(ValidationError):
        await container.accounts.connect(
            clean_session, user, label="Account", email=email, password=VALID_PASSWORD
        )


async def test_an_empty_label_is_refused(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    with pytest.raises(ValidationError):
        await container.accounts.connect(
            clean_session, user, label="   ", email=VALID_EMAIL, password=VALID_PASSWORD
        )


async def test_an_empty_password_is_refused(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    with pytest.raises(ValidationError):
        await container.accounts.connect(
            clean_session, user, label="Account", email=VALID_EMAIL, password=""
        )


async def test_consent_is_required_before_connecting(
    clean_session: AsyncSession, container: Container
) -> None:
    pending = await UserRepository(clean_session).create(
        telegram_id=999100, status=UserStatus.PENDING_CONSENT
    )
    with pytest.raises(ConsentRequiredError):
        await container.accounts.ensure_can_connect(clean_session, pending)


async def test_the_account_quota_is_enforced(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    database: Database,
    portal: FakePortal,
) -> None:
    from tests.support.portal import build_container, make_settings

    limited = build_container(
        make_settings(container.settings, max_portal_accounts_per_user=1), database, portal
    )
    await connect_account(clean_session, limited, user)
    with pytest.raises(QuotaExceededError):
        await limited.accounts.ensure_can_connect(clean_session, user)


async def test_repeated_connection_attempts_are_rate_limited(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    database: Database,
    portal: FakePortal,
) -> None:
    from tests.support.portal import build_container, make_settings

    limited = build_container(
        make_settings(container.settings, connect_rate_limit_per_hour=2), database, portal
    )
    for _ in range(2):
        await limited.accounts.ensure_can_connect(clean_session, user)
    with pytest.raises(RateLimitedError):
        await limited.accounts.ensure_can_connect(clean_session, user)


async def test_repeated_failures_lock_the_flow_and_warn_the_user(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    database: Database,
    portal: FakePortal,
) -> None:
    """Wrong passwords must not be retried indefinitely."""
    from tests.support.portal import build_container, make_settings

    strict = build_container(
        make_settings(container.settings, connect_max_failures=2), database, portal
    )
    for _ in range(2):
        with pytest.raises(AuthenticationFailed):
            await strict.accounts.connect(
                clean_session,
                user,
                label="Account",
                email=VALID_EMAIL,
                password="password-sbagliata",
            )
    with pytest.raises(RateLimitedError):
        await strict.accounts.connect(
            clean_session,
            user,
            label="Account",
            email=VALID_EMAIL,
            password="password-sbagliata",
        )
    events = await NotificationRepository(clean_session).list_for_user(
        user.id, page=0, page_size=10
    )
    assert events.total == 1
    assert events.items[0].event_type is NotificationEventType.REAUTH_REQUIRED


# --- re-authentication --------------------------------------------------------
async def test_a_stored_session_the_portal_refuses_triggers_a_fresh_login(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    portal: FakePortal,
) -> None:
    """A jar the portal rejects must not block the account for ever.

    The stored session is verified before use; when that verification fails
    with a server error the account has to log in again, otherwise every later
    request keeps failing with no way back.
    """
    account = await connect_account(clean_session, container, user)
    portal.stored_session_is_refused = True

    async with container.sessions.authenticated_session(clean_session, account) as portal_session:
        assert await container.portal_client.verify_session(portal_session) is True

    logins = [entry for entry in portal.requests if entry == "POST /login"]
    assert len(logins) >= 2, f"the account never logged in again: {portal.requests}"


async def test_reauthentication_rotates_the_credentials_and_resumes_watches(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    portal: FakePortal,
) -> None:
    account = await connect_account(clean_session, container, user)
    accounts = PortalAccountRepository(clean_session)
    await accounts.mark_auth_failure(account, error_code="AUTH", max_failures=5)
    assert account.status is PortalAccountStatus.REAUTH_REQUIRED

    watches = WatchRepository(clean_session)
    watch = await make_watch(clean_session, user, account)
    watch.status = WatchStatus.PAUSED
    watch.completed_reason = "REAUTH_REQUIRED"
    await watches.flush()

    portal.valid_credentials = (OTHER_EMAIL, VALID_PASSWORD)
    refreshed = await container.accounts.reauthenticate(
        clean_session, user, account, email=OTHER_EMAIL, password=VALID_PASSWORD
    )
    assert refreshed.id == account.id
    assert refreshed.status is PortalAccountStatus.ACTIVE
    assert refreshed.email == OTHER_EMAIL

    reloaded = await watches.get_any(watch.id)
    assert reloaded is not None
    assert reloaded.status is WatchStatus.ACTIVE


# --- management ---------------------------------------------------------------
async def test_disabling_an_account_pauses_its_watches(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    account = await connect_account(clean_session, container, user)
    watch = await make_watch(clean_session, user, account)
    affected = await container.accounts.set_enabled(clean_session, account, enabled=False)
    assert affected == 1
    reloaded = await WatchRepository(clean_session).get_any(watch.id)
    assert reloaded is not None
    assert reloaded.status is WatchStatus.PAUSED
    assert account.status is PortalAccountStatus.DISABLED


async def test_removing_an_account_erases_credentials_and_deletes_watches(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    account = await connect_account(clean_session, container, user)
    watch = await make_watch(clean_session, user, account)
    removed = await container.accounts.remove(clean_session, account)
    assert removed == 1
    assert account.password is None
    assert account.cookie_jar is None
    assert account.status is PortalAccountStatus.DELETED
    assert await WatchRepository(clean_session).get_any(watch.id) is None


async def test_test_login_refreshes_the_session(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    portal: FakePortal,
) -> None:
    account = await connect_account(clean_session, container, user)
    portal.session_expired = True
    assert await container.accounts.test_login(clean_session, account) is True
    assert portal.session_expired is False


async def test_patient_sync_deactivates_missing_patients(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    account = await connect_account(clean_session, container, user)
    patients = PatientRepository(clean_session)
    extra = await patients.upsert(
        account_id=account.id,
        tax_code="EEEFFF03C03C003C",
        display_name="Non più presente",
        relation=None,
        now=utc_now(),
    )
    result = await container.accounts.sync_patients(clean_session, account)
    assert result.synchronised == 2
    assert result.deactivated == 1
    reloaded = await patients.get(extra.id)
    assert reloaded is not None
    assert reloaded.active is False


async def test_an_account_cannot_be_opened_by_another_user(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    from app.domain.errors import NotFoundError

    account = await make_account(clean_session, user)
    intruder = await UserRepository(clean_session).create(telegram_id=999200)
    with pytest.raises(NotFoundError):
        await container.accounts.require_account(clean_session, intruder, account.id)


# --- a lost login is always reported -----------------------------------------
def _break_the_stored_login(portal: FakePortal) -> None:
    """Make the stored jar unusable and the stored credentials rejected."""
    portal.session_expired = True
    portal.valid_credentials = ("nessuno@example.invalid", "x" * 12)


async def _reauth_events(session: AsyncSession, user: TelegramUser) -> list[NotificationEventType]:
    page = await NotificationRepository(session).list_for_user(user.id, page=0, page_size=20)
    return [
        event.event_type
        for event in page.items
        if event.event_type is NotificationEventType.REAUTH_REQUIRED
    ]


async def test_the_appointment_sync_reports_a_lost_login(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    portal: FakePortal,
) -> None:
    """The hourly sync used to suspend the account without telling anyone.

    It is the job that touches every active account, so it is usually the first
    to notice that the portal no longer accepts the stored credentials. An
    account with no monitor at all was left silently unusable.
    """
    account = await connect_account(clean_session, container, user)
    account.last_sync_at = None
    await PortalAccountRepository(clean_session).flush()
    _break_the_stored_login(portal)

    task = AppointmentSyncTask(container.settings, container.appointments, container.notifications)
    assert await task.run(clean_session, limit=5) == 0

    assert account.status is PortalAccountStatus.REAUTH_REQUIRED
    assert await _reauth_events(clean_session, user) == [NotificationEventType.REAUTH_REQUIRED]


async def _fail_the_login(
    session: AsyncSession, container: Container, account: PortalAccount
) -> None:
    """Drive one refused login, then let the account be tried again.

    An account stops being usable after the first failure, so the status is put
    back deliberately: the point of the loop is the notification, not the guard.
    """
    with pytest.raises(AuthenticationFailed):
        async with container.sessions.authenticated_session(session, account):
            pass  # pragma: no cover - the context manager raises on entry
    account.status = PortalAccountStatus.ACTIVE


async def test_one_outage_produces_one_message(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    portal: FakePortal,
) -> None:
    """Repeated failures at the same status must not spam the chat."""
    account = await connect_account(clean_session, container, user)
    _break_the_stored_login(portal)

    await _fail_the_login(clean_session, container, account)
    await _fail_the_login(clean_session, container, account)

    assert await _reauth_events(clean_session, user) == [NotificationEventType.REAUTH_REQUIRED]


async def test_being_locked_out_is_reported_even_after_the_first_warning(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    portal: FakePortal,
) -> None:
    """Being locked out is the message that matters most: it must not dedupe.

    It says the retries have stopped and only the user can fix it, so it has to
    survive the deduplication that silences the failures leading up to it.
    """
    account = await connect_account(clean_session, container, user)
    _break_the_stored_login(portal)

    for _ in range(MAX_AUTH_FAILURES):
        await _fail_the_login(clean_session, container, account)

    page = await NotificationRepository(clean_session).list_for_user(user.id, page=0, page_size=10)
    statuses = {
        str(item.payload.get("status", ""))
        for item in page.items
        if item.event_type is NotificationEventType.REAUTH_REQUIRED
    }
    assert statuses == {
        PortalAccountStatus.REAUTH_REQUIRED.value,
        PortalAccountStatus.LOCKED.value,
    }


async def test_a_loss_after_a_successful_reauthentication_is_reported_again(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    portal: FakePortal,
) -> None:
    """Deduplication must not swallow the second outage.

    The subject carries the last successful login, so re-authenticating opens a
    new one instead of silencing the account for the whole retention window.
    """
    account = await connect_account(clean_session, container, user)
    _break_the_stored_login(portal)
    with pytest.raises(AuthenticationFailed):
        async with container.sessions.authenticated_session(clean_session, account):
            pass  # pragma: no cover - the context manager raises on entry
    assert await _reauth_events(clean_session, user) == [NotificationEventType.REAUTH_REQUIRED]

    portal.reset()
    await container.accounts.reauthenticate(
        clean_session, user, account, email=VALID_EMAIL, password=VALID_PASSWORD
    )
    assert account.status is PortalAccountStatus.ACTIVE
    # utc_now() has one-second precision, so a reconnection in the same second
    # as the previous login would reuse its subject. Only a test is that fast.
    account.last_login_at = utc_now() + timedelta(seconds=1)
    await PortalAccountRepository(clean_session).flush()

    _break_the_stored_login(portal)
    with pytest.raises(AuthenticationFailed):
        async with container.sessions.authenticated_session(clean_session, account):
            pass  # pragma: no cover - the context manager raises on entry

    assert await _reauth_events(clean_session, user) == [
        NotificationEventType.REAUTH_REQUIRED,
        NotificationEventType.REAUTH_REQUIRED,
    ]


# --- recovering without a human ----------------------------------------------
async def _refused_account(
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    *,
    failed_ago: timedelta = timedelta(hours=2),
) -> PortalAccount:
    """An account the portal refused, with its credentials still stored."""
    account = await connect_account(session, container, user)
    accounts = PortalAccountRepository(session)
    await accounts.mark_auth_failure(account, error_code="AUTHENTICATION_FAILED", max_failures=5)
    assert account.status is PortalAccountStatus.REAUTH_REQUIRED
    account.last_error_at = utc_now() - failed_ago
    await accounts.flush()
    return account


def _recovery_task(container: Container) -> AccountRecoveryTask:
    return AccountRecoveryTask(container.settings, container.sessions, container.notifications)


async def test_a_refused_account_comes_back_on_its_own(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    portal: FakePortal,
) -> None:
    """The stored credentials are retried without asking the user anything."""
    account = await _refused_account(clean_session, container, user)
    watch = await make_watch(clean_session, user, account)
    watch.status = WatchStatus.PAUSED
    watch.completed_reason = "REAUTH_REQUIRED"
    await WatchRepository(clean_session).flush()

    assert (await _recovery_task(container).run(clean_session)).recovered == 1

    assert account.status is PortalAccountStatus.ACTIVE
    assert account.consecutive_auth_failures == 0
    assert account.cookie_jar
    assert watch.status is WatchStatus.ACTIVE
    assert watch.completed_reason is None
    page = await NotificationRepository(clean_session).list_for_user(user.id, page=0, page_size=10)
    assert NotificationEventType.ACCOUNT_RECOVERED in {item.event_type for item in page.items}


async def test_recovery_leaves_a_recent_failure_alone(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
) -> None:
    """The retry is spaced out, so a failure from a moment ago is not retried."""
    account = await _refused_account(clean_session, container, user, failed_ago=timedelta(0))
    assert (await _recovery_task(container).run(clean_session)).recovered == 0
    assert account.status is PortalAccountStatus.REAUTH_REQUIRED


async def test_an_unreachable_portal_does_not_spend_a_recovery_attempt(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    portal: FakePortal,
) -> None:
    """A portal that cannot answer says nothing about the credentials.

    Counting it would walk the account into the lockout that hands it to a
    human, which is exactly what this is supposed to avoid.
    """
    account = await _refused_account(clean_session, container, user)
    failures_before = account.consecutive_auth_failures
    portal.transient_failures = 50

    assert (await _recovery_task(container).run(clean_session)).recovered == 0
    assert account.status is PortalAccountStatus.REAUTH_REQUIRED
    assert account.consecutive_auth_failures == failures_before


async def test_a_password_that_really_changed_still_asks_the_user(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    portal: FakePortal,
) -> None:
    """Automation cannot invent the new password: the attempt must be spent."""
    account = await _refused_account(clean_session, container, user)
    failures_before = account.consecutive_auth_failures
    _break_the_stored_login(portal)

    assert (await _recovery_task(container).run(clean_session)).recovered == 0
    assert account.consecutive_auth_failures == failures_before + 1


async def test_a_locked_account_is_never_retried_automatically(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
) -> None:
    """Retrying a locked account risks locking the real portal account."""
    account = await _refused_account(clean_session, container, user)
    account.status = PortalAccountStatus.LOCKED
    await PortalAccountRepository(clean_session).flush()

    assert (await _recovery_task(container).run(clean_session)).recovered == 0
    assert account.status is PortalAccountStatus.LOCKED


async def test_re_enabling_an_account_resumes_the_monitors_it_paused(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
) -> None:
    """Disabling paused them, so enabling has to bring them back.

    They used to stay paused, because only a monitor stopped by an
    authentication failure was ever resumed.
    """
    watches = WatchRepository(clean_session)
    account = await connect_account(clean_session, container, user)
    watch = await make_watch(clean_session, user, account)
    assert await container.accounts.set_enabled(clean_session, account, enabled=False) == 1
    paused = await watches.get_any(watch.id)
    assert paused is not None and paused.status is WatchStatus.PAUSED

    assert await container.accounts.set_enabled(clean_session, account, enabled=True) == 1
    resumed = await watches.get_any(watch.id)
    assert resumed is not None
    assert resumed.status is WatchStatus.ACTIVE
    assert resumed.completed_reason is None


async def test_recovery_hands_back_one_account_at_a_time(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    portal: FakePortal,
) -> None:
    """The scheduler relies on this to give each login its own transaction.

    Each selected row stays locked until the caller commits, so the number of
    attempts has to be honest: the loop stops on a pass that found nothing, and
    would otherwise either quit early or keep asking for work that is not there.
    """
    recoverable = await _refused_account(clean_session, container, user)
    # A second account due at the same time. Its stored password is not the one
    # the portal accepts, so it is attempted and refused rather than recovered.
    doomed = await make_account(clean_session, user, email=OTHER_EMAIL, label="Secondo")
    accounts = PortalAccountRepository(clean_session)
    await accounts.mark_auth_failure(doomed, error_code="AUTHENTICATION_FAILED", max_failures=5)
    doomed.last_error_at = utc_now() - timedelta(hours=1)
    await accounts.flush()
    refusals = doomed.consecutive_auth_failures

    task = _recovery_task(container)
    assert (await task.run(clean_session, limit=1)).attempted == 1
    assert (await task.run(clean_session, limit=1)).attempted == 1
    # One is active again and the other has just failed, so neither is due.
    assert (await task.run(clean_session, limit=1)).attempted == 0

    assert recoverable.status is PortalAccountStatus.ACTIVE
    assert doomed.status is PortalAccountStatus.REAUTH_REQUIRED
    assert doomed.consecutive_auth_failures == refusals + 1
