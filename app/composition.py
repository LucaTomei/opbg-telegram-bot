"""Composition root.

Every process (``web``, ``bot``, ``worker``) builds its dependencies here so the
wiring is explicit and testable, and so the security services required by the
ORM descriptors are configured exactly once at start-up.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings, get_settings
from app.db.session import Database
from app.logging import configure_logging
from app.portal.circuit_breaker import BreakerPolicy, CircuitBreakerService
from app.portal.client import PortalClient
from app.portal.rate_limit import (
    AsyncRateLimiter,
    DatabasePortalGate,
    PortalGate,
    RateLimitPolicy,
)
from app.security.context import configure_security_from_settings
from app.services.accounts import AccountService
from app.services.admin import AdminService
from app.services.appointments import AppointmentService
from app.services.bookings import BookingService
from app.services.confirmations import ConfirmationService
from app.services.notifications import NotificationService
from app.services.portal_sessions import PortalSessionManager
from app.services.search import SearchService
from app.services.users import InviteService, UserService
from app.services.watches import WatchService


@dataclass(slots=True)
class Container:
    """Fully wired application services for one process."""

    settings: Settings
    database: Database
    portal_client: PortalClient
    breaker: CircuitBreakerService
    sessions: PortalSessionManager
    users: UserService
    invites: InviteService
    accounts: AccountService
    search: SearchService
    bookings: BookingService
    appointments: AppointmentService
    watches: WatchService
    notifications: NotificationService
    confirmations: ConfirmationService
    admin: AdminService

    async def dispose(self) -> None:
        await self.database.dispose()


def build_rate_limit_policy(settings: Settings) -> RateLimitPolicy:
    return RateLimitPolicy(
        max_concurrency=settings.portal_max_concurrency,
        min_interval_seconds=settings.portal_min_interval_ms / 1000,
        per_account_min_interval_seconds=max(settings.portal_min_interval_ms / 1000, 1.0),
        global_window_seconds=60,
        global_max_calls_per_window=max(settings.portal_max_concurrency * 30, 30),
    )


def build_breaker_policy(settings: Settings) -> BreakerPolicy:
    return BreakerPolicy(
        account_failure_threshold=settings.breaker_account_failure_threshold,
        account_cooldown_seconds=settings.breaker_account_cooldown_seconds,
        global_failure_threshold=settings.breaker_global_failure_threshold,
        global_cooldown_seconds=settings.breaker_global_cooldown_seconds,
    )


def build_container(
    *,
    service_name: str,
    settings: Settings | None = None,
    database: Database | None = None,
    gate: PortalGate | None = None,
    configure_logs: bool = True,
) -> Container:
    """Build the dependency graph shared by every entrypoint."""
    resolved = settings or get_settings()
    if configure_logs:
        configure_logging(service=service_name, level=resolved.app_log_level)
    configure_security_from_settings(resolved)

    db = database or Database(resolved)
    policy = build_rate_limit_policy(resolved)
    limiter = AsyncRateLimiter(policy)
    portal_gate = gate or DatabasePortalGate(db.session_factory, policy)
    client = PortalClient(resolved, limiter=limiter, gate=portal_gate)
    breaker = CircuitBreakerService(build_breaker_policy(resolved))
    sessions = PortalSessionManager(resolved, client, breaker)
    search = SearchService(resolved, client, sessions)

    notifications = NotificationService(resolved)
    return Container(
        settings=resolved,
        database=db,
        portal_client=client,
        breaker=breaker,
        sessions=sessions,
        users=UserService(resolved),
        invites=InviteService(resolved),
        accounts=AccountService(resolved, client, sessions, notifications),
        search=search,
        bookings=BookingService(resolved, client, search),
        appointments=AppointmentService(resolved, client, sessions),
        watches=WatchService(resolved),
        notifications=notifications,
        confirmations=ConfirmationService(resolved),
        admin=AdminService(resolved, breaker),
    )
