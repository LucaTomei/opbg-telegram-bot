"""Administrative operations.

Every read of unmasked personal data and every action performed on behalf of
another user produces an immutable audit entry.  Secrets — passwords, cookies,
CSRF values, tokens and encryption keys — are never returned by any method in
this module.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models.accounts import PortalAccount
from app.db.models.users import TelegramUser
from app.db.repositories.accounts import PatientRepository, PortalAccountRepository
from app.db.repositories.audit import AuditRepository
from app.db.repositories.base import Page, utc_now
from app.db.repositories.bookings import AppointmentRepository, BookingAttemptRepository
from app.db.repositories.notifications import NotificationRepository
from app.db.repositories.system import (
    BackupStatusRepository,
    HeartbeatRepository,
    PortalCallLogRepository,
    SystemEventRepository,
)
from app.db.repositories.users import InviteRepository, UserRepository
from app.db.repositories.watches import SeenSlotRepository, WatchRepository, WatchRunLogRepository
from app.domain.enums import (
    AuditAction,
    BreakerScope,
    BreakerState,
    NotificationStatus,
    PortalAccountStatus,
    Severity,
    UserRole,
    UserStatus,
    WatchStatus,
)
from app.domain.errors import NotFoundError, PermissionDeniedError, ValidationError
from app.logging import get_logger, new_correlation_id
from app.portal.circuit_breaker import CircuitBreakerService
from app.security.redaction import mask_email, mask_person_name, mask_tax_code
from app.security.tokens import content_hash
from app.version import APP_VERSION, SCHEMA_VERSION

logger = get_logger(__name__)


def require_admin(user: TelegramUser) -> None:
    """Guard used by every administrative entry point."""
    if user.role is not UserRole.ADMIN or user.status is not UserStatus.ACTIVE:
        raise PermissionDeniedError("administrative privileges are required")


@dataclass(frozen=True, slots=True)
class SystemStatus:
    """Operational snapshot shown on the administration dashboard."""

    app_version: str
    schema_version: str
    environment: str
    users_by_status: dict[str, int]
    accounts_total: int
    accounts_needing_reauth: int
    watches_active: int
    watches_due: int
    watches_leased: int
    notifications_pending: int
    notifications_failed: int
    unresolved_events: int
    critical_events: int
    workers: list[dict[str, Any]]
    breakers: list[dict[str, Any]]
    portal_calls_last_minute: int
    last_backup: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class MaskedUser:
    """A user rendered with masked identifiers for list screens."""

    user: TelegramUser
    display_name: str
    username: str


class AdminService:
    """Read models and mutating actions available to administrators."""

    def __init__(self, settings: Settings, breaker: CircuitBreakerService) -> None:
        self._settings = settings
        self._breaker = breaker

    # --- read models -----------------------------------------------------
    async def list_users(
        self,
        session: AsyncSession,
        admin: TelegramUser,
        *,
        page: int,
        page_size: int,
        status: UserStatus | None = None,
        role: UserRole | None = None,
        telegram_id: int | None = None,
    ) -> Page[TelegramUser]:
        require_admin(admin)
        return await UserRepository(session).list_users(
            page=page, page_size=page_size, status=status, role=role, telegram_id=telegram_id
        )

    @staticmethod
    def mask_user(user: TelegramUser) -> MaskedUser:
        """Render a user without exposing the raw Telegram profile."""
        first = user.first_name or ""
        last = user.last_name or ""
        full = f"{first} {last}".strip()
        username = user.username or ""
        return MaskedUser(
            user=user,
            display_name=mask_person_name(full) if full else f"ID {user.telegram_id}",
            username=f"@{username[:1]}***" if username else "-",
        )

    @staticmethod
    def mask_account(account: PortalAccount) -> str:
        email = account.email
        return mask_email(email) if email else "-"

    async def reveal_user(
        self,
        session: AsyncSession,
        admin: TelegramUser,
        *,
        user_id: uuid.UUID,
        reason: str,
        correlation_id: str | None = None,
    ) -> dict[str, str]:
        """Reveal the unmasked profile of a user and audit the access."""
        require_admin(admin)
        users = UserRepository(session)
        target = await users.get(user_id)
        if target is None:
            raise NotFoundError("user not found")
        await AuditRepository(session).record(
            admin_user_id=admin.id,
            action=AuditAction.REVEAL_PII,
            target_type="telegram_user",
            target_id=target.id.hex,
            reason=reason,
            correlation_id=correlation_id or new_correlation_id(),
        )
        return {
            "telegram_id": str(target.telegram_id),
            "username": target.username or "-",
            "first_name": target.first_name or "-",
            "last_name": target.last_name or "-",
            "status": target.status.value,
            "role": target.role.value,
        }

    async def reveal_account(
        self,
        session: AsyncSession,
        admin: TelegramUser,
        *,
        account_id: uuid.UUID,
        reason: str,
        correlation_id: str | None = None,
    ) -> dict[str, str]:
        """Reveal the account e-mail; credentials are never returned."""
        require_admin(admin)
        accounts = PortalAccountRepository(session)
        account = await accounts.get(account_id)
        if account is None:
            raise NotFoundError("portal account not found")
        await AuditRepository(session).record(
            admin_user_id=admin.id,
            action=AuditAction.REVEAL_PII,
            target_type="portal_account",
            target_id=account.id.hex,
            reason=reason,
            correlation_id=correlation_id or new_correlation_id(),
        )
        return {
            "label": account.label,
            "email": account.email or "-",
            "status": account.status.value,
            "last_login_at": "-" if account.last_login_at is None else str(account.last_login_at),
            "last_sync_at": "-" if account.last_sync_at is None else str(account.last_sync_at),
            "last_error_code": account.last_error_code or "-",
        }

    async def reveal_patient(
        self,
        session: AsyncSession,
        admin: TelegramUser,
        *,
        patient_id: uuid.UUID,
        reason: str,
        correlation_id: str | None = None,
    ) -> dict[str, str]:
        require_admin(admin)
        patients = PatientRepository(session)
        patient = await patients.get(patient_id)
        if patient is None:
            raise NotFoundError("patient not found")
        await AuditRepository(session).record(
            admin_user_id=admin.id,
            action=AuditAction.REVEAL_PII,
            target_type="patient",
            target_id=patient.id.hex,
            reason=reason,
            correlation_id=correlation_id or new_correlation_id(),
        )
        return {
            "display_name": patient.display_name or "-",
            "tax_code": patient.external_tax_code or "-",
            "relationship": patient.relation or "-",
            "active": str(patient.active),
        }

    @staticmethod
    def masked_patient(patient: Any) -> dict[str, str]:
        name = getattr(patient, "display_name", None)
        tax_code = getattr(patient, "external_tax_code", None)
        return {
            "display_name": mask_person_name(name) if name else "-",
            "tax_code": mask_tax_code(tax_code) if tax_code else "-",
        }

    # --- user management -------------------------------------------------
    async def set_user_status(
        self,
        session: AsyncSession,
        admin: TelegramUser,
        *,
        user_id: uuid.UUID,
        status: UserStatus,
        reason: str,
        correlation_id: str | None = None,
    ) -> TelegramUser:
        """Suspend, block or reactivate a user and pause their monitors."""
        require_admin(admin)
        users = UserRepository(session)
        target = await users.get(user_id)
        if target is None:
            raise NotFoundError("user not found")
        if target.id == admin.id and status in (UserStatus.BLOCKED, UserStatus.SUSPENDED):
            raise ValidationError("an administrator cannot block themselves")
        before = content_hash(target.status.value)
        target.status = status
        await users.flush()
        if status in (UserStatus.SUSPENDED, UserStatus.BLOCKED, UserStatus.DELETED):
            watches = WatchRepository(session)
            page = await watches.list_all(
                page=0, page_size=500, user_id=target.id, status=WatchStatus.ACTIVE
            )
            for watch in page.items:
                await watches.set_status(watch, WatchStatus.PAUSED, reason="ADMIN_SUSPENDED")
        await AuditRepository(session).record(
            admin_user_id=admin.id,
            action=AuditAction.USER_STATUS_CHANGED,
            target_type="telegram_user",
            target_id=target.id.hex,
            reason=reason,
            before_hash=before,
            after_hash=content_hash(status.value),
            correlation_id=correlation_id or new_correlation_id(),
        )
        return target

    async def set_account_status(
        self,
        session: AsyncSession,
        admin: TelegramUser,
        *,
        account_id: uuid.UUID,
        status: PortalAccountStatus,
        reason: str,
        correlation_id: str | None = None,
    ) -> PortalAccount:
        require_admin(admin)
        accounts = PortalAccountRepository(session)
        account = await accounts.get(account_id)
        if account is None:
            raise NotFoundError("portal account not found")
        before = content_hash(account.status.value)
        await accounts.set_status(account, status)
        if status is not PortalAccountStatus.ACTIVE:
            await WatchRepository(session).suspend_for_account(account.id, reason="ADMIN_DISABLED")
        await AuditRepository(session).record(
            admin_user_id=admin.id,
            action=AuditAction.ACCOUNT_STATUS_CHANGED,
            target_type="portal_account",
            target_id=account.id.hex,
            reason=reason,
            before_hash=before,
            after_hash=content_hash(status.value),
            correlation_id=correlation_id or new_correlation_id(),
        )
        return account

    async def set_watch_status(
        self,
        session: AsyncSession,
        admin: TelegramUser,
        *,
        watch_id: uuid.UUID,
        status: WatchStatus,
        reason: str,
        correlation_id: str | None = None,
    ) -> None:
        require_admin(admin)
        watches = WatchRepository(session)
        watch = await watches.get_any(watch_id)
        if watch is None:
            raise NotFoundError("monitor not found")
        before = content_hash(watch.status.value)
        if status is WatchStatus.ACTIVE:
            watch.consecutive_errors = 0
            watch.next_check_at = utc_now()
        await watches.set_status(watch, status, reason="ADMIN")
        await AuditRepository(session).record(
            admin_user_id=admin.id,
            action=AuditAction.WATCH_STATUS_CHANGED,
            target_type="booking_watch",
            target_id=watch.id.hex,
            reason=reason,
            before_hash=before,
            after_hash=content_hash(status.value),
            correlation_id=correlation_id or new_correlation_id(),
        )

    async def set_watch_notifications(
        self,
        session: AsyncSession,
        admin: TelegramUser,
        *,
        watch_id: uuid.UUID,
        enabled: bool,
        reason: str,
        correlation_id: str | None = None,
    ) -> None:
        require_admin(admin)
        watches = WatchRepository(session)
        watch = await watches.get_any(watch_id)
        if watch is None:
            raise NotFoundError("monitor not found")
        before = content_hash(str(watch.notifications_enabled))
        watch.notifications_enabled = enabled
        await watches.flush()
        await AuditRepository(session).record(
            admin_user_id=admin.id,
            action=AuditAction.WATCH_NOTIFICATIONS_CHANGED,
            target_type="booking_watch",
            target_id=watch.id.hex,
            reason=reason,
            before_hash=before,
            after_hash=content_hash(str(enabled)),
            correlation_id=correlation_id or new_correlation_id(),
        )

    async def run_watch_now(
        self,
        session: AsyncSession,
        admin: TelegramUser,
        *,
        watch_id: uuid.UUID,
        reason: str,
        correlation_id: str | None = None,
    ) -> None:
        require_admin(admin)
        watches = WatchRepository(session)
        watch = await watches.get_any(watch_id)
        if watch is None:
            raise NotFoundError("monitor not found")
        if watch.status is not WatchStatus.ACTIVE:
            raise ValidationError("only an active monitor can be executed now")
        watch.next_check_at = utc_now()
        watch.last_run_now_at = utc_now()
        await watches.flush()
        await AuditRepository(session).record(
            admin_user_id=admin.id,
            action=AuditAction.WATCH_RUN_NOW,
            target_type="booking_watch",
            target_id=watch.id.hex,
            reason=reason,
            correlation_id=correlation_id or new_correlation_id(),
        )

    async def override_breaker(
        self,
        session: AsyncSession,
        admin: TelegramUser,
        *,
        scope: BreakerScope,
        subject: str,
        state: BreakerState,
        reason: str,
        correlation_id: str | None = None,
    ) -> None:
        require_admin(admin)
        record = await self._breaker.force_state(
            session,
            scope=scope,
            subject=subject,
            state=state,
            admin_user_id=admin.id,
            reason=reason,
        )
        await AuditRepository(session).record(
            admin_user_id=admin.id,
            action=AuditAction.BREAKER_OVERRIDE,
            target_type="circuit_breaker",
            target_id=f"{scope.value}:{subject}"[:64],
            reason=reason,
            after_hash=content_hash(record.state.value),
            correlation_id=correlation_id or new_correlation_id(),
        )

    async def resolve_event(
        self,
        session: AsyncSession,
        admin: TelegramUser,
        *,
        event_id: uuid.UUID,
        note: str,
        correlation_id: str | None = None,
    ) -> None:
        require_admin(admin)
        events = SystemEventRepository(session)
        event = await events.get(event_id)
        if event is None:
            raise NotFoundError("system event not found")
        await events.resolve(event, admin_user_id=admin.id, note=note)
        await AuditRepository(session).record(
            admin_user_id=admin.id,
            action=AuditAction.SYSTEM_EVENT_RESOLVED,
            target_type="system_event",
            target_id=event.id.hex,
            reason=note,
            correlation_id=correlation_id or new_correlation_id(),
        )

    async def record_delegated_action(
        self,
        session: AsyncSession,
        admin: TelegramUser,
        *,
        action: AuditAction,
        target_type: str,
        target_id: str,
        reason: str,
        correlation_id: str | None = None,
    ) -> None:
        """Audit an action performed on behalf of another user."""
        require_admin(admin)
        await AuditRepository(session).record(
            admin_user_id=admin.id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            reason=reason,
            correlation_id=correlation_id or new_correlation_id(),
        )

    # --- dashboards ------------------------------------------------------
    async def system_status(
        self, session: AsyncSession, admin: TelegramUser, *, now: datetime | None = None
    ) -> SystemStatus:
        require_admin(admin)
        moment = now or utc_now()
        users = UserRepository(session)
        accounts = PortalAccountRepository(session)
        watches = WatchRepository(session)
        notifications = NotificationRepository(session)
        events = SystemEventRepository(session)
        heartbeats = HeartbeatRepository(session)
        calls = PortalCallLogRepository(session)
        backups = BackupStatusRepository(session)

        by_status = await users.count_by_status()
        accounts_page = await accounts.list_all(page=0, page_size=1)
        reauth_page = await accounts.list_all(
            page=0, page_size=1, status=PortalAccountStatus.REAUTH_REQUIRED
        )
        active_watches = await watches.list_all(page=0, page_size=1, status=WatchStatus.ACTIVE)
        notification_counts = await notifications.count_by_status()
        breakers = await self._breaker.list_states(session)
        last_backup = await backups.latest()

        return SystemStatus(
            app_version=APP_VERSION,
            schema_version=SCHEMA_VERSION,
            environment=self._settings.app_env,
            users_by_status={key.value: value for key, value in by_status.items()},
            accounts_total=accounts_page.total,
            accounts_needing_reauth=reauth_page.total,
            watches_active=active_watches.total,
            watches_due=await watches.due_count(now=moment),
            watches_leased=await watches.leased_count(now=moment),
            notifications_pending=notification_counts.get(NotificationStatus.PENDING, 0),
            notifications_failed=notification_counts.get(NotificationStatus.FAILED, 0),
            unresolved_events=await events.count_unresolved(),
            critical_events=await events.count_unresolved(severity=Severity.CRITICAL),
            workers=[
                {
                    "worker_id": worker.worker_id,
                    "last_seen_at": worker.last_seen_at.isoformat(),
                    "app_version": worker.app_version,
                    "stale": (moment - worker.last_seen_at).total_seconds()
                    > self._settings.worker_heartbeat_stale_seconds,
                }
                for worker in await heartbeats.list_workers()
            ],
            breakers=[
                {
                    "scope": record.scope.value,
                    "subject": record.subject_key,
                    "state": record.state.value,
                    "failures": record.failure_count,
                    "reopen_at": None if record.reopen_at is None else record.reopen_at.isoformat(),
                }
                for record in breakers
            ],
            portal_calls_last_minute=await calls.recent_count(window_seconds=60),
            last_backup=None
            if last_backup is None
            else {
                "outcome": last_backup.outcome,
                "started_at": last_backup.started_at.isoformat(),
                "artifact": last_backup.artifact_name or "-",
                "size_bytes": last_backup.size_bytes or 0,
            },
        )

    async def list_invites(
        self, session: AsyncSession, admin: TelegramUser, *, page: int, page_size: int
    ) -> Page[Any]:
        require_admin(admin)
        return await InviteRepository(session).list_invites(page=page, page_size=page_size)

    async def list_accounts(
        self,
        session: AsyncSession,
        admin: TelegramUser,
        *,
        page: int,
        page_size: int,
        status: PortalAccountStatus | None = None,
        user_id: uuid.UUID | None = None,
    ) -> Page[PortalAccount]:
        require_admin(admin)
        return await PortalAccountRepository(session).list_all(
            page=page, page_size=page_size, status=status, user_id=user_id
        )

    async def list_patients(
        self,
        session: AsyncSession,
        admin: TelegramUser,
        *,
        page: int,
        page_size: int,
        account_id: uuid.UUID | None = None,
    ) -> Page[Any]:
        require_admin(admin)
        return await PatientRepository(session).list_all(
            page=page, page_size=page_size, account_id=account_id
        )

    async def list_watches(
        self,
        session: AsyncSession,
        admin: TelegramUser,
        *,
        page: int,
        page_size: int,
        status: WatchStatus | None = None,
        user_id: uuid.UUID | None = None,
        account_id: uuid.UUID | None = None,
    ) -> Page[Any]:
        require_admin(admin)
        return await WatchRepository(session).list_all(
            page=page, page_size=page_size, status=status, user_id=user_id, account_id=account_id
        )

    async def list_attempts(
        self,
        session: AsyncSession,
        admin: TelegramUser,
        *,
        page: int,
        page_size: int,
        **filters: Any,
    ) -> Page[Any]:
        require_admin(admin)
        return await BookingAttemptRepository(session).list_all(
            page=page, page_size=page_size, **filters
        )

    async def list_appointments(
        self,
        session: AsyncSession,
        admin: TelegramUser,
        *,
        page: int,
        page_size: int,
        **filters: Any,
    ) -> Page[Any]:
        require_admin(admin)
        return await AppointmentRepository(session).list_all(
            page=page, page_size=page_size, **filters
        )

    async def list_notifications(
        self,
        session: AsyncSession,
        admin: TelegramUser,
        *,
        page: int,
        page_size: int,
        **filters: Any,
    ) -> Page[Any]:
        require_admin(admin)
        return await NotificationRepository(session).list_all(
            page=page, page_size=page_size, **filters
        )

    async def list_events(
        self,
        session: AsyncSession,
        admin: TelegramUser,
        *,
        page: int,
        page_size: int,
        **filters: Any,
    ) -> Page[Any]:
        require_admin(admin)
        return await SystemEventRepository(session).list_events(
            page=page, page_size=page_size, **filters
        )

    async def list_audit(
        self,
        session: AsyncSession,
        admin: TelegramUser,
        *,
        page: int,
        page_size: int,
        **filters: Any,
    ) -> Page[Any]:
        require_admin(admin)
        return await AuditRepository(session).list_entries(
            page=page, page_size=page_size, **filters
        )

    async def list_seen_slots(
        self,
        session: AsyncSession,
        admin: TelegramUser,
        *,
        watch_id: uuid.UUID,
        page: int,
        page_size: int,
    ) -> Page[Any]:
        require_admin(admin)
        return await SeenSlotRepository(session).list_for_watch(
            watch_id, page=page, page_size=page_size
        )

    async def list_watch_runs(
        self,
        session: AsyncSession,
        admin: TelegramUser,
        *,
        watch_id: uuid.UUID,
        page: int,
        page_size: int,
    ) -> Page[Any]:
        require_admin(admin)
        return await WatchRunLogRepository(session).list_for_watch(
            watch_id, page=page, page_size=page_size
        )
