"""Account connection, re-authentication and patient synchronisation."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.composition import Container
from app.db.models.users import TelegramUser
from app.db.repositories.accounts import (
    ConnectTicketRepository,
    PatientRepository,
    PortalAccountRepository,
)
from app.db.repositories.base import utc_now
from app.db.repositories.watches import WatchRepository
from app.domain.enums import (
    ConnectTicketPurpose,
    PortalAccountStatus,
    UserStatus,
    WatchStatus,
)
from app.domain.errors import (
    AlreadyConsumedError,
    AuthenticationFailed,
    ConflictError,
    ConsentRequiredError,
    ExpiredError,
    QuotaExceededError,
    RateLimitedError,
)
from tests.support.portal import VALID_EMAIL, VALID_PASSWORD, FakePortal

OTHER_EMAIL = "secondo.utente@example.invalid"


async def connect_account(
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    *,
    email: str = VALID_EMAIL,
    label: str = "Account principale",
):
    link = await container.accounts.create_connect_link(session, user)
    tickets = ConnectTicketRepository(session)
    ticket = await tickets.get_by_token(link.url.rsplit("/", 1)[-1])
    assert ticket is not None
    return await container.accounts.complete_connection(
        session, ticket=ticket, label=label, email=email, password=VALID_PASSWORD
    )


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


async def test_the_ticket_is_consumed_after_use(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    link = await container.accounts.create_connect_link(session=clean_session, user=user)
    token = link.url.rsplit("/", 1)[-1]
    tickets = ConnectTicketRepository(clean_session)
    ticket = await tickets.get_by_token(token)
    assert ticket is not None
    await container.accounts.complete_connection(
        session=clean_session,
        ticket=ticket,
        label="Account",
        email=VALID_EMAIL,
        password=VALID_PASSWORD,
    )
    with pytest.raises(AlreadyConsumedError):
        await container.accounts.load_ticket(clean_session, token)


async def test_expired_ticket_is_refused(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    link = await container.accounts.create_connect_link(clean_session, user)
    token = link.url.rsplit("/", 1)[-1]
    tickets = ConnectTicketRepository(clean_session)
    ticket = await tickets.get_by_token(token)
    assert ticket is not None
    ticket.expires_at = utc_now().replace(year=2020)
    await tickets.flush()
    with pytest.raises(ExpiredError):
        await container.accounts.load_ticket(clean_session, token)


async def test_wrong_credentials_do_not_create_an_account(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    link = await container.accounts.create_connect_link(clean_session, user)
    tickets = ConnectTicketRepository(clean_session)
    ticket = await tickets.get_by_token(link.url.rsplit("/", 1)[-1])
    assert ticket is not None
    with pytest.raises(AuthenticationFailed):
        await container.accounts.complete_connection(
            session=clean_session,
            ticket=ticket,
            label="Account",
            email=VALID_EMAIL,
            password="password-sbagliata",
        )
    assert await PortalAccountRepository(clean_session).count_for_user(user.id) == 0
    assert ticket.failure_count == 1


async def test_the_same_account_cannot_be_connected_twice(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await connect_account(clean_session, container, user)
    link = await container.accounts.create_connect_link(clean_session, user)
    ticket = await ConnectTicketRepository(clean_session).get_by_token(
        link.url.rsplit("/", 1)[-1]
    )
    assert ticket is not None
    with pytest.raises(ConflictError):
        await container.accounts.complete_connection(
            session=clean_session,
            ticket=ticket,
            label="Duplicato",
            email=VALID_EMAIL.upper(),
            password=VALID_PASSWORD,
        )


async def test_consent_is_required_before_connecting(
    clean_session: AsyncSession, container: Container
) -> None:
    from app.db.repositories.users import UserRepository

    pending = await UserRepository(clean_session).create(
        telegram_id=999100, status=UserStatus.PENDING_CONSENT
    )
    with pytest.raises(ConsentRequiredError):
        await container.accounts.create_connect_link(clean_session, pending)


async def test_the_account_quota_is_enforced(
    clean_session: AsyncSession, container: Container, user: TelegramUser, database, portal
) -> None:
    from tests.support.portal import build_container, make_settings

    limited = build_container(
        make_settings(container.settings, max_portal_accounts_per_user=1), database, portal
    )
    await connect_account(clean_session, limited, user)
    with pytest.raises(QuotaExceededError):
        await limited.accounts.create_connect_link(clean_session, user)


async def test_rate_limit_blocks_repeated_attempts(
    clean_session: AsyncSession, container: Container, database, portal
) -> None:
    from tests.support.portal import build_container, make_settings

    limited = build_container(
        make_settings(container.settings, connect_rate_limit_per_hour=2), database, portal
    )
    for _ in range(2):
        await limited.accounts.enforce_connect_rate_limit(
            clean_session, scope="ip", subject="203.0.113.10"
        )
    with pytest.raises(RateLimitedError):
        await limited.accounts.enforce_connect_rate_limit(
            clean_session, scope="ip", subject="203.0.113.10"
        )


async def test_reauthentication_rotates_the_credentials_and_resumes_watches(
    clean_session: AsyncSession, container: Container, user: TelegramUser, portal: FakePortal
) -> None:
    account = await connect_account(clean_session, container, user)
    accounts = PortalAccountRepository(clean_session)
    await accounts.mark_auth_failure(account, error_code="AUTH", max_failures=5)
    assert account.status is PortalAccountStatus.REAUTH_REQUIRED

    watches = WatchRepository(clean_session)
    from tests.support.factories import make_watch

    watch = await make_watch(clean_session, user, account)
    watch.status = WatchStatus.PAUSED
    watch.completed_reason = "REAUTH_REQUIRED"
    await watches.flush()

    link = await container.accounts.create_connect_link(
        clean_session,
        user,
        purpose=ConnectTicketPurpose.REAUTH,
        portal_account_id=account.id,
    )
    ticket = await ConnectTicketRepository(clean_session).get_by_token(
        link.url.rsplit("/", 1)[-1]
    )
    assert ticket is not None
    portal.valid_credentials = (OTHER_EMAIL, VALID_PASSWORD)
    refreshed = await container.accounts.complete_connection(
        session=clean_session,
        ticket=ticket,
        label="Account",
        email=OTHER_EMAIL,
        password=VALID_PASSWORD,
    )
    assert refreshed.id == account.id
    assert refreshed.status is PortalAccountStatus.ACTIVE
    assert refreshed.email == OTHER_EMAIL

    reloaded = await watches.get_any(watch.id)
    assert reloaded is not None
    assert reloaded.status is WatchStatus.ACTIVE


async def test_disabling_an_account_pauses_its_watches(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    account = await connect_account(clean_session, container, user)
    from tests.support.factories import make_watch

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
    from tests.support.factories import make_watch

    watch = await make_watch(clean_session, user, account)
    removed = await container.accounts.remove(clean_session, account)
    assert removed == 1
    assert account.password is None
    assert account.cookie_jar is None
    assert account.status is PortalAccountStatus.DELETED
    assert await WatchRepository(clean_session).get_any(watch.id) is None


async def test_test_login_refreshes_the_session(
    clean_session: AsyncSession, container: Container, user: TelegramUser, portal: FakePortal
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
