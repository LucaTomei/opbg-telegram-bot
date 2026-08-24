"""Portal account connection, re-authentication and patient synchronisation."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from hashlib import sha256

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models.accounts import ConnectTicket, PortalAccount
from app.db.models.users import TelegramUser
from app.db.repositories.accounts import (
    ConnectRateLimitRepository,
    ConnectTicketRepository,
    PatientRepository,
    PortalAccountRepository,
)
from app.db.repositories.base import utc_now
from app.db.repositories.watches import WatchRepository
from app.domain.enums import (
    ConnectTicketPurpose,
    PortalAccountStatus,
    WatchStatus,
)
from app.domain.errors import (
    AlreadyConsumedError,
    AuthenticationFailed,
    ConflictError,
    ConsentRequiredError,
    ExpiredError,
    NotFoundError,
    QuotaExceededError,
    RateLimitedError,
    ValidationError,
)
from app.logging import get_logger
from app.portal.client import PortalClient
from app.portal.models import PortalCredentials
from app.security.context import get_encryption
from app.security.tokens import generate_token
from app.services.portal_sessions import PortalSessionManager

logger = get_logger(__name__)

MAX_LABEL_LENGTH = 64


@dataclass(frozen=True, slots=True)
class ConnectLink:
    """A one-time HTTPS link the user opens to submit credentials."""

    url: str
    expires_in_minutes: int
    ticket_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class PatientSyncResult:
    synchronised: int
    deactivated: int


def fingerprint_subject(value: str) -> bytes:
    """Hash a rate-limit subject so raw addresses are never persisted."""
    return sha256(value.encode("utf-8")).digest()


class AccountService:
    """Everything a user can do with their portal accounts."""

    def __init__(
        self,
        settings: Settings,
        client: PortalClient,
        sessions: PortalSessionManager,
    ) -> None:
        self._settings = settings
        self._client = client
        self._sessions = sessions

    # --- connection ------------------------------------------------------
    async def create_connect_link(
        self,
        session: AsyncSession,
        user: TelegramUser,
        *,
        purpose: ConnectTicketPurpose = ConnectTicketPurpose.CONNECT,
        portal_account_id: uuid.UUID | None = None,
        suggested_label: str | None = None,
    ) -> ConnectLink:
        """Issue a short-lived, single-use HTTPS connection ticket."""
        if not user.has_consented:
            raise ConsentRequiredError("consent is required before connecting an account")
        accounts = PortalAccountRepository(session)
        if purpose is ConnectTicketPurpose.CONNECT:
            existing = await accounts.count_for_user(user.id)
            if existing >= self._settings.max_portal_accounts_per_user:
                raise QuotaExceededError(
                    "the maximum number of portal accounts has been reached",
                    limit=str(self._settings.max_portal_accounts_per_user),
                )
        tickets = ConnectTicketRepository(session)
        await tickets.invalidate_for_user(user.id)
        token = generate_token()
        ticket = await tickets.create(
            user_id=user.id,
            token=token,
            purpose=purpose,
            ttl_minutes=self._settings.connect_ticket_ttl_minutes,
            portal_account_id=portal_account_id,
            suggested_label=suggested_label,
        )
        return ConnectLink(
            url=self._settings.connect_url(token),
            expires_in_minutes=self._settings.connect_ticket_ttl_minutes,
            ticket_id=ticket.id,
        )

    async def load_ticket(self, session: AsyncSession, token: str) -> ConnectTicket:
        """Resolve a connection ticket, rejecting expired or consumed ones."""
        tickets = ConnectTicketRepository(session)
        ticket = await tickets.get_by_token(token)
        if ticket is None:
            raise NotFoundError("connection ticket is unknown")
        if ticket.consumed_at is not None:
            raise AlreadyConsumedError("connection ticket was already used")
        if ticket.expires_at <= utc_now():
            raise ExpiredError("connection ticket expired")
        if ticket.failure_count >= self._settings.connect_max_failures:
            raise RateLimitedError("too many failed attempts for this ticket")
        return ticket

    async def enforce_connect_rate_limit(
        self, session: AsyncSession, *, scope: str, subject: str
    ) -> None:
        """Apply the sliding rate limit protecting the public connect form."""
        limits = ConnectRateLimitRepository(session)
        now = utc_now()
        window_start = now.replace(minute=0, second=0, microsecond=0)
        exceeded = await limits.hit(
            scope=scope,
            subject_hash=fingerprint_subject(subject),
            window_start=window_start,
            limit=self._settings.connect_rate_limit_per_hour,
        )
        if exceeded:
            raise RateLimitedError("too many connection attempts", retry_after_seconds=3600)

    async def complete_connection(
        self,
        session: AsyncSession,
        *,
        ticket: ConnectTicket,
        label: str,
        email: str,
        password: str,
    ) -> PortalAccount:
        """Verify credentials against the portal and store them encrypted.

        Nothing is persisted before the portal confirms the login, and the
        ticket is consumed regardless of the outcome of this call.
        """
        cleaned_label = label.strip()[:MAX_LABEL_LENGTH]
        if not cleaned_label:
            raise ValidationError("a label is required")
        if "@" not in email or len(email) > 254:
            raise ValidationError("the e-mail address is not valid")
        if not password:
            raise ValidationError("the password is required")

        accounts = PortalAccountRepository(session)
        tickets = ConnectTicketRepository(session)
        encryption = get_encryption()

        if ticket.purpose is ConnectTicketPurpose.CONNECT:
            duplicate = await accounts.find_by_email(ticket.telegram_user_id, email)
            if duplicate is not None:
                await tickets.consume(ticket)
                raise ConflictError("this portal account is already connected")

        credentials = PortalCredentials(email=email, password=password)
        try:
            cookies, metadata = await self._sessions.verify_credentials(
                credentials, account_key=ticket.id.hex
            )
        except AuthenticationFailed:
            await tickets.register_failure(ticket)
            raise

        if ticket.purpose is ConnectTicketPurpose.REAUTH and ticket.portal_account_id is not None:
            account = await accounts.get(ticket.portal_account_id)
            if account is None:
                raise NotFoundError("portal account no longer exists")
            await accounts.rotate_credentials(
                account,
                email=email,
                password=password,
                encryption_key_version=encryption.active_key_version,
            )
        else:
            account = await accounts.create(
                user_id=ticket.telegram_user_id,
                label=cleaned_label,
                email=email,
                password=password,
                encryption_key_version=encryption.active_key_version,
            )
        await accounts.store_session(account, cookies=cookies, metadata=metadata)
        await tickets.consume(ticket)
        await self.sync_patients(session, account)
        await self._resume_watches(session, account)
        logger.info(
            "portal account connected",
            extra={
                "event_code": "ACCOUNT_CONNECTED",
                "account_id": account.id.hex,
                "purpose": ticket.purpose.value,
            },
        )
        return account

    async def _resume_watches(self, session: AsyncSession, account: PortalAccount) -> int:
        """Reactivate the monitors paused by a previous authentication failure."""
        watches = WatchRepository(session)
        page = await watches.list_all(
            page=0, page_size=500, account_id=account.id, status=WatchStatus.PAUSED
        )
        resumed = 0
        now = utc_now()
        for watch in page.items:
            if watch.completed_reason != "REAUTH_REQUIRED":
                continue
            watch.status = WatchStatus.ACTIVE
            watch.completed_reason = None
            watch.consecutive_errors = 0
            watch.next_check_at = now
            resumed += 1
        await watches.flush()
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
        cleaned = label.strip()[:MAX_LABEL_LENGTH]
        if not cleaned:
            raise ValidationError("a label is required")
        await PortalAccountRepository(session).rename(account, cleaned)

    async def set_enabled(
        self, session: AsyncSession, account: PortalAccount, *, enabled: bool
    ) -> int:
        """Enable or disable an account, pausing its monitors when disabled."""
        accounts = PortalAccountRepository(session)
        if enabled:
            await accounts.set_status(account, PortalAccountStatus.ACTIVE)
            return await self._resume_watches(session, account)
        await accounts.set_status(account, PortalAccountStatus.DISABLED)
        suspended = await WatchRepository(session).suspend_for_account(
            account.id, reason="ACCOUNT_DISABLED"
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
        """Soft delete an account after suspending its monitors."""
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
