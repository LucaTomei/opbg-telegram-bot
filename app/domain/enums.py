"""Domain enumerations shared by persistence, services and presentation."""

from __future__ import annotations

from enum import StrEnum, unique


@unique
class UserRole(StrEnum):
    USER = "USER"
    ADMIN = "ADMIN"


@unique
class UserStatus(StrEnum):
    PENDING_CONSENT = "PENDING_CONSENT"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    BLOCKED = "BLOCKED"
    DELETED = "DELETED"


@unique
class PortalAccountStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    LOCKED = "LOCKED"
    DELETED = "DELETED"


@unique
class ConnectTicketPurpose(StrEnum):
    CONNECT = "CONNECT"
    REAUTH = "REAUTH"


@unique
class SearchType(StrEnum):
    SERVICE = "SERVICE"
    DOCTOR = "DOCTOR"


@unique
class LocationMode(StrEnum):
    ALL_DYNAMIC = "ALL_DYNAMIC"
    SELECTED = "SELECTED"


@unique
class DateMode(StrEnum):
    FIXED = "FIXED"
    ROLLING = "ROLLING"


@unique
class SortStrategy(StrEnum):
    EARLIEST = "EARLIEST"
    PREFERRED_LOCATION_FIRST = "PREFERRED_LOCATION_FIRST"
    DATE_TIME_ASC = "DATE_TIME_ASC"


@unique
class WatchMode(StrEnum):
    NOTIFY = "NOTIFY"
    AUTOBOOK = "AUTOBOOK"
    AUTOBOOK_NOTIFY = "AUTOBOOK_NOTIFY"

    @property
    def notifies_availability(self) -> bool:
        return self in (WatchMode.NOTIFY, WatchMode.AUTOBOOK_NOTIFY)

    @property
    def books(self) -> bool:
        return self in (WatchMode.AUTOBOOK, WatchMode.AUTOBOOK_NOTIFY)


@unique
class WatchStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"
    DELETED = "DELETED"


@unique
class WatchCompletionPolicy(StrEnum):
    COMPLETE_ON_SUCCESS = "COMPLETE_ON_SUCCESS"
    CONTINUE_AFTER_SUCCESS = "CONTINUE_AFTER_SUCCESS"


@unique
class BookingDraftStatus(StrEnum):
    OPEN = "OPEN"
    CONFIRMED = "CONFIRMED"
    ABANDONED = "ABANDONED"
    EXPIRED = "EXPIRED"


@unique
class BookingAttemptStatus(StrEnum):
    STARTED = "STARTED"
    VERIFYING = "VERIFYING"
    SUCCEEDED = "SUCCEEDED"
    SLOT_LOST = "SLOT_LOST"
    REJECTED = "REJECTED"
    AMBIGUOUS = "AMBIGUOUS"
    FAILED = "FAILED"

    @property
    def is_terminal(self) -> bool:
        return self in (
            BookingAttemptStatus.SUCCEEDED,
            BookingAttemptStatus.SLOT_LOST,
            BookingAttemptStatus.REJECTED,
            BookingAttemptStatus.AMBIGUOUS,
            BookingAttemptStatus.FAILED,
        )


@unique
class BookingStage(StrEnum):
    CREATED = "CREATED"
    SESSION_READY = "SESSION_READY"
    PATIENT_SELECTED = "PATIENT_SELECTED"
    SEARCH_SUBMITTED = "SEARCH_SUBMITTED"
    AVAILABILITY_REFRESHED = "AVAILABILITY_REFRESHED"
    SLOT_SELECTED = "SLOT_SELECTED"
    CONFIRM_SENT = "CONFIRM_SENT"
    RECONCILED = "RECONCILED"


@unique
class AppointmentStatus(StrEnum):
    PENDING = "PENDING"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    UNKNOWN = "UNKNOWN"


@unique
class NotificationEventType(StrEnum):
    NEW_SLOT = "NEW_SLOT"
    SLOT_LOST = "SLOT_LOST"
    AUTOBOOK_SUCCESS = "AUTOBOOK_SUCCESS"
    AUTOBOOK_FAILED = "AUTOBOOK_FAILED"
    AUTOBOOK_AMBIGUOUS = "AUTOBOOK_AMBIGUOUS"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    WATCH_SUSPENDED = "WATCH_SUSPENDED"
    APPOINTMENT_DETECTED = "APPOINTMENT_DETECTED"
    CANCELLATION_SUCCESS = "CANCELLATION_SUCCESS"
    CANCELLATION_FAILED = "CANCELLATION_FAILED"
    PDF_UNAVAILABLE = "PDF_UNAVAILABLE"
    WATCH_AMBIGUOUS_SEARCH = "WATCH_AMBIGUOUS_SEARCH"
    LOCATION_DISAPPEARED = "LOCATION_DISAPPEARED"
    ADMIN_ACTION = "ADMIN_ACTION"

    @property
    def is_critical(self) -> bool:
        """Critical events bypass quiet hours and the global notification switch."""
        return self in (
            NotificationEventType.AUTOBOOK_SUCCESS,
            NotificationEventType.AUTOBOOK_FAILED,
            NotificationEventType.AUTOBOOK_AMBIGUOUS,
            NotificationEventType.REAUTH_REQUIRED,
            NotificationEventType.CANCELLATION_SUCCESS,
            NotificationEventType.CANCELLATION_FAILED,
            NotificationEventType.ADMIN_ACTION,
        )


@unique
class NotificationStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    SUPPRESSED = "SUPPRESSED"


@unique
class ConfirmationAction(StrEnum):
    BOOK_SLOT = "BOOK_SLOT"
    CANCEL_APPOINTMENT = "CANCEL_APPOINTMENT"
    DELETE_WATCH = "DELETE_WATCH"
    DELETE_ACCOUNT = "DELETE_ACCOUNT"
    DELETE_PROFILE = "DELETE_PROFILE"
    ENABLE_AUTOBOOK = "ENABLE_AUTOBOOK"
    ADMIN_BLOCK_USER = "ADMIN_BLOCK_USER"
    ADMIN_DELEGATED_BOOK = "ADMIN_DELEGATED_BOOK"
    ADMIN_DELEGATED_CANCEL = "ADMIN_DELEGATED_CANCEL"
    ADMIN_REVEAL_PII = "ADMIN_REVEAL_PII"
    ADMIN_BREAKER_OVERRIDE = "ADMIN_BREAKER_OVERRIDE"
    ADMIN_REVOKE_INVITE = "ADMIN_REVOKE_INVITE"
    ADMIN_DELETE_USER_DATA = "ADMIN_DELETE_USER_DATA"


@unique
class AuditAction(StrEnum):
    ADMIN_SESSION_START = "ADMIN_SESSION_START"
    REVEAL_PII = "REVEAL_PII"
    INVITE_CREATED = "INVITE_CREATED"
    INVITE_REVOKED = "INVITE_REVOKED"
    USER_STATUS_CHANGED = "USER_STATUS_CHANGED"
    ACCOUNT_STATUS_CHANGED = "ACCOUNT_STATUS_CHANGED"
    WATCH_STATUS_CHANGED = "WATCH_STATUS_CHANGED"
    WATCH_NOTIFICATIONS_CHANGED = "WATCH_NOTIFICATIONS_CHANGED"
    WATCH_RUN_NOW = "WATCH_RUN_NOW"
    DELEGATED_BOOKING = "DELEGATED_BOOKING"
    DELEGATED_CANCELLATION = "DELEGATED_CANCELLATION"
    DELEGATED_PDF_DOWNLOAD = "DELEGATED_PDF_DOWNLOAD"
    BREAKER_OVERRIDE = "BREAKER_OVERRIDE"
    DATA_DELETED = "DATA_DELETED"
    CONFIG_CHANGED = "CONFIG_CHANGED"
    SYSTEM_EVENT_RESOLVED = "SYSTEM_EVENT_RESOLVED"
    FORCED_SYNC = "FORCED_SYNC"


@unique
class Severity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@unique
class BreakerScope(StrEnum):
    GLOBAL = "GLOBAL"
    ACCOUNT = "ACCOUNT"


@unique
class BreakerState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@unique
class PdfDeliveryMode(StrEnum):
    TELEGRAM = "TELEGRAM"
    ONE_TIME_LINK = "ONE_TIME_LINK"


WEEKDAY_LABELS_IT: tuple[str, ...] = (
    "Lunedì",
    "Martedì",
    "Mercoledì",
    "Giovedì",
    "Venerdì",
    "Sabato",
    "Domenica",
)

ALL_WEEKDAYS_MASK = 0b1111111
