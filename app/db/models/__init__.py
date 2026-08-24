"""SQLAlchemy models.

Importing this package registers every mapper on :class:`app.db.base.Base`,
which Alembic autogeneration and the integration test suite rely on.
"""

from __future__ import annotations

from app.db.base import Base
from app.db.models.accounts import (
    ConnectRateLimit,
    ConnectTicket,
    DownloadToken,
    Patient,
    PortalAccount,
)
from app.db.models.bookings import AppointmentCache, BookingAttempt, CancellationAttempt
from app.db.models.conversations import (
    BookingDraft,
    CallbackConsumption,
    ConfirmationNonce,
    ConversationState,
)
from app.db.models.notifications import NotificationEvent
from app.db.models.system import (
    AdminAuditLog,
    BackupStatus,
    CircuitBreakerStateRecord,
    PortalCallLog,
    SystemEvent,
    WorkerHeartbeat,
)
from app.db.models.users import (
    InviteCode,
    InviteRedemption,
    TelegramUser,
    UserSettingsAudit,
)
from app.db.models.watches import BookingWatch, SeenSlot, WatchLocation, WatchRunLog

__all__ = [
    "AdminAuditLog",
    "AppointmentCache",
    "BackupStatus",
    "Base",
    "BookingAttempt",
    "BookingDraft",
    "BookingWatch",
    "CallbackConsumption",
    "CancellationAttempt",
    "CircuitBreakerStateRecord",
    "ConfirmationNonce",
    "ConnectRateLimit",
    "ConnectTicket",
    "ConversationState",
    "DownloadToken",
    "InviteCode",
    "InviteRedemption",
    "NotificationEvent",
    "Patient",
    "PortalAccount",
    "PortalCallLog",
    "SeenSlot",
    "SystemEvent",
    "TelegramUser",
    "UserSettingsAudit",
    "WatchLocation",
    "WatchRunLog",
    "WorkerHeartbeat",
]
