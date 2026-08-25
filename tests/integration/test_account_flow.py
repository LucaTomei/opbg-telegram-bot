"""Account connection, re-authentication and patient synchronisation."""

from __future__ import annotations

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
