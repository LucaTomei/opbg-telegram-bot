"""Portal account connection, re-authentication and patient synchronisation.

Credentials are collected inside the Telegram conversation.  The handler that
receives them deletes the message immediately, never writes the password to the
conversation state and calls this service, which verifies the login against the
portal before anything is persisted.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from hashlib import sha256

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models.accounts import PortalAccount
from app.db.models.users import TelegramUser
from app.db.repositories.accounts import (
    ConnectRateLimitRepository,
    PatientRepository,
    PortalAccountRepository,
)
from app.db.repositories.base import utc_now
from app.db.repositories.watches import WatchRepository
from app.domain.entities import CookieJar
from app.domain.enums import (
    NotificationEventType,
    PortalAccountStatus,
)
from app.domain.errors import (
    AuthenticationFailed,
    ConflictError,
    ConsentRequiredError,
    NotFoundError,
    QuotaExceededError,
    RateLimitedError,
    ValidationError,
)
from app.logging import get_logger
from app.portal.client import PortalClient
from app.portal.models import PortalCredentials
from app.security.context import get_encryption
from app.services.notifications import NotificationService
from app.services.portal_sessions import PortalSessionManager

logger = get_logger(__name__)

MAX_LABEL_LENGTH = 64
MAX_EMAIL_LENGTH = 254
MAX_PASSWORD_LENGTH = 255

#: Rate-limit scopes protecting the credential steps of the conversation.
SCOPE_ATTEMPT = "attempt"
SCOPE_FAILURE = "failure"

#: Why a monitor was paused, and therefore what may resume it.
REASON_REAUTH_REQUIRED = "REAUTH_REQUIRED"
REASON_ACCOUNT_DISABLED = "ACCOUNT_DISABLED"


@dataclass(frozen=True, slots=True)
class PatientSyncResult:
    synchronised: int
    deactivated: int


def fingerprint_subject(value: str) -> bytes:
    """Hash a rate-limit subject so no raw identifier is persisted."""
    return sha256(value.encode("utf-8")).digest()


def clean_label(label: str) -> str:
    cleaned = " ".join(label.strip().split())[:MAX_LABEL_LENGTH]
    if not cleaned:
        raise ValidationError("a label is required")
    return cleaned


def clean_email(email: str) -> str:
    cleaned = email.strip()
    if "@" not in cleaned or len(cleaned) > MAX_EMAIL_LENGTH or " " in cleaned:
        raise ValidationError("the e-mail address is not valid")
    return cleaned


def check_password(password: str) -> str:
    if not password or len(password) > MAX_PASSWORD_LENGTH:
        raise ValidationError("the password is not valid")
    return password


class AccountService:
    """Everything a user can do with their portal accounts."""

    def __init__(
        self,
        settings: Settings,
        client: PortalClient,
        sessions: PortalSessionManager,
        notifications: NotificationService | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._sessions = sessions
        self._notifications = notifications

    # --- guards ----------------------------------------------------------
    async def ensure_can_connect(self, session: AsyncSession, user: TelegramUser) -> None:
        """Check consent, quota and rate limit before asking for credentials."""
        if not user.has_consented:
            raise ConsentRequiredError("consent is required before connecting an account")
        accounts = PortalAccountRepository(session)
        if await accounts.count_for_user(user.id) >= self._settings.max_portal_accounts_per_user:
            raise QuotaExceededError(
                "the maximum number of portal accounts has been reached",
                limit=str(self._settings.max_portal_accounts_per_user),
            )
        await self._hit_limit(
            session,
            scope=SCOPE_ATTEMPT,
            subject=user.id.hex,
            limit=self._settings.connect_rate_limit_per_hour,
        )

    async def _hit_limit(
        self, session: AsyncSession, *, scope: str, subject: str, limit: int
    ) -> None:
        now = utc_now()
        exceeded = await ConnectRateLimitRepository(session).hit(
            scope=scope,
            subject_hash=fingerprint_subject(subject),
            window_start=now.replace(minute=0, second=0, microsecond=0),
            limit=limit,
        )
        if exceeded:
            raise RateLimitedError("too many attempts", retry_after_seconds=3600)

    async def _register_failure(self, session: AsyncSession, user: TelegramUser) -> None:
        """Count a wrong password and warn the user when the limit is reached."""
        try:
            await self._hit_limit(
                session,
                scope=SCOPE_FAILURE,
                subject=user.id.hex,
                limit=self._settings.connect_max_failures,
            )
        except RateLimitedError:
            if self._notifications is not None:
                await self._notifications.queue(
                    session,
                    user=user,
                    event_type=NotificationEventType.REAUTH_REQUIRED,
                    subject=f"{user.id.hex}:connect-locked",
                    payload={
                        "account_id": user.id.hex,
                        "watch_name": "",
                        "error_code": "CONNECT_LOCKED",
                    },
                )
            logger.warning(
                "credential attempts locked after repeated failures",
                extra={"event_code": "CONNECT_LOCKED"},
            )
            raise

    # --- connection ------------------------------------------------------
    async def connect(
        self,
        session: AsyncSession,
        user: TelegramUser,
        *,
        label: str,
        email: str,
        password: str,
    ) -> PortalAccount:
        """Verify the credentials against the portal and store them encrypted.

        Nothing is persisted before the portal confirms the login.
        """
        cleaned_label = clean_label(label)
        cleaned_email = clean_email(email)
        check_password(password)

        accounts = PortalAccountRepository(session)
        if await accounts.find_by_email(user.id, cleaned_email) is not None:
            raise ConflictError("this portal account is already connected")

        cookies, metadata = await self._verify(session, user, cleaned_email, password)
        account = await accounts.create(
            user_id=user.id,
            label=cleaned_label,
            email=cleaned_email,
            password=password,
            encryption_key_version=get_encryption().active_key_version,
        )
        await accounts.store_session(account, cookies=cookies, metadata=metadata)
        await self.sync_patients(session, account)
        logger.info(
            "portal account connected",
            extra={"event_code": "ACCOUNT_CONNECTED", "account_id": account.id.hex},
        )
        return account

    async def reauthenticate(
        self,
        session: AsyncSession,
        user: TelegramUser,
        account: PortalAccount,
        *,
        email: str,
        password: str,
    ) -> PortalAccount:
        """Replace the stored credentials and resume the suspended monitors."""
        cleaned_email = clean_email(email)
        check_password(password)

        accounts = PortalAccountRepository(session)
        cookies, metadata = await self._verify(session, user, cleaned_email, password)
        await accounts.rotate_credentials(
            account,
            email=cleaned_email,
            password=password,
            encryption_key_version=get_encryption().active_key_version,
        )
        await accounts.store_session(account, cookies=cookies, metadata=metadata)
        await self.sync_patients(session, account)
        await self._resume_watches(session, account)
        logger.info(
            "portal credentials refreshed",
            extra={"event_code": "ACCOUNT_REAUTHENTICATED", "account_id": account.id.hex},
        )
        return account

    async def _verify(
        self, session: AsyncSession, user: TelegramUser, email: str, password: str
    ) -> tuple[CookieJar, dict[str, object]]:
        credentials = PortalCredentials(email=email, password=password)
        try:
            return await self._sessions.verify_credentials(credentials, account_key=user.id.hex)
        except AuthenticationFailed:
            await self._register_failure(session, user)
            raise

    async def _resume_watches(
        self,
        session: AsyncSession,
        account: PortalAccount,
        *,
        reasons: tuple[str, ...] = (REASON_REAUTH_REQUIRED,),
    ) -> int:
        """Reactivate the monitors those reasons had paused.

        A monitor the user paused themselves is left alone: only what the
        account's own trouble stopped is restarted.
        """
        watches = WatchRepository(session)
        resumed = 0
        for reason in reasons:
            resumed += len(await watches.resume_for_account(account.id, reason=reason))
        return resumed

    # --- management ------------------------------------------------------
    async def list_accounts(self, session: AsyncSession, user: TelegramUser) -> list[PortalAccount]:
        return await PortalAccountRepository(session).list_for_user(user.id)

    async def require_account(
        self, session: AsyncSession, user: TelegramUser, account_id: uuid.UUID
    ) -> PortalAccount:
        account = await PortalAccountRepository(session).get_owned(account_id, user.id)
        if account is None:
            raise NotFoundError("portal account not found")
        return account

    async def rename(self, session: AsyncSession, account: PortalAccount, *, label: str) -> None:
        await PortalAccountRepository(session).rename(account, clean_label(label))

    async def set_enabled(
        self, session: AsyncSession, account: PortalAccount, *, enabled: bool
    ) -> int:
        """Enable or disable an account, pausing its monitors when disabled."""
        accounts = PortalAccountRepository(session)
        if enabled:
            await accounts.set_status(account, PortalAccountStatus.ACTIVE)
            # Re-enabling has to undo what disabling did, or the monitors stay
            # paused until the user reopens each one of them by hand.
            return await self._resume_watches(
                session, account, reasons=(REASON_ACCOUNT_DISABLED, REASON_REAUTH_REQUIRED)
            )
        await accounts.set_status(account, PortalAccountStatus.DISABLED)
        suspended = await WatchRepository(session).suspend_for_account(
            account.id, reason=REASON_ACCOUNT_DISABLED
        )
        return len(suspended)

    async def test_login(self, session: AsyncSession, account: PortalAccount) -> bool:
        """Force a session verification, refreshing the login when needed."""
        async with self._sessions.authenticated_session(session, account) as portal_session:
            verified = await self._client.verify_session(portal_session)
        if verified:
            account.last_login_at = utc_now()
            await PortalAccountRepository(session).flush()
        return verified

    async def sync_patients(
        self, session: AsyncSession, account: PortalAccount
    ) -> PatientSyncResult:
        """Refresh the patient list of one account from the portal."""
        patients_repo = PatientRepository(session)
        accounts = PortalAccountRepository(session)
        async with self._sessions.authenticated_session(session, account) as portal_session:
            portal_patients = await self._client.open_lapi_wizard(portal_session)
        now = utc_now()
        seen: list[str] = []
        for portal_patient in portal_patients:
            await patients_repo.upsert(
                account_id=account.id,
                tax_code=portal_patient.tax_code,
                display_name=portal_patient.display_name,
                relation=portal_patient.relation,
                now=now,
            )
            seen.append(portal_patient.tax_code)
        deactivated = await patients_repo.deactivate_missing(account.id, seen_tax_codes=seen)
        account.last_sync_at = now
        await accounts.flush()
        logger.info(
            "patients synchronised",
            extra={
                "event_code": "PATIENTS_SYNCED",
                "account_id": account.id.hex,
                "count": len(seen),
                "deactivated": deactivated,
            },
        )
        return PatientSyncResult(synchronised=len(seen), deactivated=deactivated)

    async def remove(self, session: AsyncSession, account: PortalAccount) -> int:
        """Soft delete an account after removing its monitors."""
        watches = WatchRepository(session)
        suspended = await watches.suspend_for_account(account.id, reason="ACCOUNT_REMOVED")
        for watch in suspended:
            await watches.soft_delete(watch)
        await PortalAccountRepository(session).soft_delete(account)
        logger.info(
            "portal account removed",
            extra={
                "event_code": "ACCOUNT_REMOVED",
                "account_id": account.id.hex,
                "watches": len(suspended),
            },
        )
        return len(suspended)

    async def watches_affected_by_removal(
        self, session: AsyncSession, account: PortalAccount
    ) -> int:
        return await WatchRepository(session).count_active_for_account(account.id)
