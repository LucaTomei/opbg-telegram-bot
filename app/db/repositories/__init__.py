"""Repository layer.

All SQL lives here.  Handlers and services depend on these classes, never on
SQLAlchemy constructs directly.
"""

from __future__ import annotations

from app.db.repositories.accounts import (
    ConnectRateLimitRepository,
    ConnectTicketRepository,
    DownloadTokenRepository,
    PatientRepository,
    PortalAccountRepository,
)
from app.db.repositories.audit import AuditLogImmutableError, AuditRepository
from app.db.repositories.base import BaseRepository, Page, utc_now
from app.db.repositories.bookings import (
    AppointmentRepository,
    BookingAttemptRepository,
    CancellationAttemptRepository,
)
from app.db.repositories.conversations import (
    BookingDraftRepository,
    CallbackConsumptionRepository,
    ConfirmationNonceRepository,
    ConversationStateRepository,
)
from app.db.repositories.notifications import NotificationRepository
from app.db.repositories.system import (
    BackupStatusRepository,
    HeartbeatRepository,
    PortalCallLogRepository,
    SystemEventRepository,
)
from app.db.repositories.users import InviteRepository, UserRepository
from app.db.repositories.watches import (
    SeenSlotRepository,
    WatchRepository,
    WatchRunLogRepository,
)

__all__ = [
    "AppointmentRepository",
    "AuditLogImmutableError",
    "AuditRepository",
    "BackupStatusRepository",
    "BaseRepository",
    "BookingAttemptRepository",
    "BookingDraftRepository",
    "CallbackConsumptionRepository",
    "CancellationAttemptRepository",
    "ConfirmationNonceRepository",
    "ConnectRateLimitRepository",
    "ConnectTicketRepository",
    "ConversationStateRepository",
    "DownloadTokenRepository",
    "HeartbeatRepository",
    "InviteRepository",
    "NotificationRepository",
    "Page",
    "PatientRepository",
    "PortalAccountRepository",
    "PortalCallLogRepository",
    "SeenSlotRepository",
    "SystemEventRepository",
    "UserRepository",
    "WatchRepository",
    "WatchRunLogRepository",
    "utc_now",
]
