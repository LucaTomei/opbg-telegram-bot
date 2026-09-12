"""Portal session lifecycle.

Cookies belong to exactly one portal account and are stored encrypted.  A
session is reused when it still verifies, refreshed with the stored credentials
otherwise, and never retried in a loop when the credentials themselves are
rejected: the account is suspended and the user is asked to re-authenticate.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models.accounts import PortalAccount
from app.db.repositories.accounts import PortalAccountRepository
from app.db.repositories.users import UserRepository
from app.domain.entities import CookieJar
from app.domain.enums import BreakerScope, NotificationEventType, PortalAccountStatus
from app.domain.errors import (
    AccountSuspendedError,
    AuthenticationFailed,
    PortalCaptchaRequired,
    PortalContractChanged,
    PortalError,
    PortalRateLimited,
    PortalUnavailable,
    ReauthenticationRequired,
)
from app.logging import get_logger
from app.portal.circuit_breaker import CircuitBreakerService
from app.portal.client import PortalClient, PortalSession
from app.portal.models import PortalCredentials
from app.services.notifications import NotificationService

logger = get_logger(__name__)

MAX_AUTH_FAILURES = 3


def reauth_subject(account: PortalAccount) -> str:
    """Deduplication subject for the "login lost" notification of one account.

    It carries the last successful login, so every failure inside the same
    outage collapses into one message, while a loss that happens after the user
    has re-authenticated is a new subject and notifies again.
    """
    stamp = account.last_login_at.isoformat() if account.last_login_at else "never"
    return f"{account.id.hex}:reauth:{stamp}"


@dataclass(frozen=True, slots=True)
class SessionOutcome:
    """Result of preparing a portal session."""

    reauthenticated: bool


class PortalSessionManager:
    """Loads, verifies and refreshes the portal session of one account."""

    def __init__(
        self,
        settings: Settings,
        client: PortalClient,
        breaker: CircuitBreakerService,
        notifications: NotificationService | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._breaker = breaker
        self._notifications = notifications

    @asynccontextmanager
    async def authenticated_session(
        self, session: AsyncSession, account: PortalAccount
    ) -> AsyncIterator[PortalSession]:
        """Yield a verified portal session for ``account``.

        The circuit breaker is consulted before any traffic, and both the
        account-scoped and the global breaker are updated with the outcome.
        """
        if not account.is_usable:
            raise AccountSuspendedError("portal account is not usable", status=account.status.value)
        accounts = PortalAccountRepository(session)
        subject = account.id.hex
        await self._breaker.ensure_closed(session, scope=BreakerScope.GLOBAL)
        await self._breaker.ensure_closed(session, scope=BreakerScope.ACCOUNT, subject=subject)

        stored = account.cookie_jar
        # A list is the current scoped format; a dict is a jar stored before the
        # scope was kept, and is still loadable.
        stored_cookies = stored if isinstance(stored, list | dict) and stored else None
        async with self._client.session(
            account_key=subject, cookies=stored_cookies
        ) as portal_session:
            try:
                if not stored_cookies or not await self._client.verify_session(portal_session):
                    await self._login(accounts, account, portal_session)
                yield portal_session
            except (AuthenticationFailed, ReauthenticationRequired) as exc:
                await self._handle_auth_failure(session, accounts, account, exc)
                raise
            except (PortalUnavailable, PortalRateLimited, PortalContractChanged) as exc:
                await self._breaker.record_failure(
                    session, scope=BreakerScope.GLOBAL, error_code=exc.code
                )
                await self._breaker.record_failure(
                    session, scope=BreakerScope.ACCOUNT, subject=subject, error_code=exc.code
                )
                await accounts.mark_error(account, error_code=exc.code)
                raise
            except PortalCaptchaRequired as exc:
                await accounts.mark_auth_failure(account, error_code=exc.code, max_failures=1)
                await self._notify_login_lost(session, account, error_code=exc.code)
                raise
            else:
                await self._breaker.record_success(session, scope=BreakerScope.GLOBAL)
                await self._breaker.record_success(
                    session, scope=BreakerScope.ACCOUNT, subject=subject
                )
            finally:
                await self._persist_cookies(accounts, account, portal_session)

    async def _login(
        self,
        accounts: PortalAccountRepository,
        account: PortalAccount,
        portal_session: PortalSession,
    ) -> None:
        password = account.password
        email = account.email
        if not password or not email:
            account.status = PortalAccountStatus.REAUTH_REQUIRED
            await accounts.flush()
            raise ReauthenticationRequired("stored credentials are missing")
        portal_session.clear_cookies()
        credentials = PortalCredentials(email=email, password=password)
        del password, email
        result = await self._client.login(portal_session, credentials)
        await accounts.store_session(
            account, cookies=result.cookies, metadata=result.account_metadata
        )
        logger.info(
            "portal session refreshed",
            extra={"event_code": "PORTAL_LOGIN_OK", "account_id": account.id.hex},
        )

    async def _handle_auth_failure(
        self,
        session: AsyncSession,
        accounts: PortalAccountRepository,
        account: PortalAccount,
        error: PortalError,
    ) -> None:
        await accounts.mark_auth_failure(
            account, error_code=error.code, max_failures=MAX_AUTH_FAILURES
        )
        await self._breaker.record_failure(
            session,
            scope=BreakerScope.ACCOUNT,
            subject=account.id.hex,
            error_code=error.code,
        )
        logger.warning(
            "portal authentication failed",
            extra={
                "event_code": "PORTAL_AUTH_FAILED",
                "account_id": account.id.hex,
                "error_code": error.code,
            },
        )
        await self._notify_login_lost(session, account, error_code=error.code)

    async def _notify_login_lost(
        self, session: AsyncSession, account: PortalAccount, *, error_code: str
    ) -> None:
        """Tell the owner that the stored login no longer works.

        Queued here, where the loss is declared, so the message reaches the user
        whichever task discovered it — a monitor, the hourly appointment sync or
        an interactive action — instead of only the paths that remember to ask.
        """
        if self._notifications is None:
            return
        user = await UserRepository(session).get(account.telegram_user_id)
        if user is None:
            return
        await self._notifications.queue(
            session,
            user=user,
            event_type=NotificationEventType.REAUTH_REQUIRED,
            subject=reauth_subject(account),
            payload={
                "account_id": account.id.hex,
                "account_label": account.label,
                "status": account.status.value,
                "error_code": error_code,
            },
            portal_account_id=account.id,
        )

    async def _persist_cookies(
        self,
        accounts: PortalAccountRepository,
        account: PortalAccount,
        portal_session: PortalSession,
    ) -> None:
        """Store the refreshed cookie jar, but only for a still valid account."""
        if account.status in (PortalAccountStatus.LOCKED, PortalAccountStatus.REAUTH_REQUIRED):
            return
        cookies = portal_session.export_cookies()
        expected = self._settings.portal_session_cookie_name
        if expected not in portal_session.cookie_names():
            # Without the session cookie the jar is useless and would only cause
            # a failed verification on the next run.
            return
        try:
            account.cookie_jar = cookies
            await accounts.flush()
        except Exception:
            # Never let the bookkeeping hide the failure that brought us here.
            logger.warning(
                "could not persist the refreshed cookie jar",
                extra={"event_code": "COOKIE_PERSIST_FAILED", "account_id": account.id.hex},
            )

    async def verify_credentials(
        self, credentials: PortalCredentials, *, account_key: str
    ) -> tuple[CookieJar, dict[str, object]]:
        """Log in with fresh credentials without touching any stored account."""
        async with self._client.session(account_key=account_key) as portal_session:
            result = await self._client.login(portal_session, credentials)
            return result.cookies, result.account_metadata

    async def load_patients(
        self, session: AsyncSession, account: PortalAccount
    ) -> tuple[object, ...]:
        async with self.authenticated_session(session, account) as portal_session:
            return await self._client.open_lapi_wizard(portal_session)


def account_subject(account_id: uuid.UUID) -> str:
    return account_id.hex
