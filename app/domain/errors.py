"""Typed domain errors.

The portal adapter raises these exceptions instead of leaking HTTP or parsing
details.  Every error carries a stable ``code`` used for metrics, persistence
and user-facing message selection, plus an already redacted ``detail``.
"""

from __future__ import annotations

from typing import Any

from app.security.redaction import redact_text


class DomainError(Exception):
    """Base class for every expected, typed application error."""

    code: str = "DOMAIN_ERROR"
    #: Whether an idempotent retry of the very same operation is acceptable.
    retryable: bool = False

    def __init__(self, detail: str = "", **context: Any) -> None:
        self.detail = redact_text(detail) if detail else ""
        self.context = {key: redact_text(str(value)) for key, value in context.items()}
        super().__init__(f"{self.code}: {self.detail}" if self.detail else self.code)

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}" if self.detail else self.code


# --- Portal ------------------------------------------------------------------


class PortalError(DomainError):
    code = "PORTAL_ERROR"


class AuthenticationFailed(PortalError):
    """Wrong credentials, or the portal refused the login form."""

    code = "AUTHENTICATION_FAILED"


class ReauthenticationRequired(PortalError):
    """The stored session is no longer usable and credentials must be refreshed."""

    code = "REAUTHENTICATION_REQUIRED"


class PortalRateLimited(PortalError):
    """The portal answered 429 or an equivalent throttling response."""

    code = "PORTAL_RATE_LIMITED"
    retryable = True

    def __init__(
        self, detail: str = "", retry_after_seconds: float | None = None, **context: Any
    ) -> None:
        super().__init__(detail, **context)
        self.retry_after_seconds: float | None = retry_after_seconds


class PortalUnavailable(PortalError):
    """Network failure, timeout or 5xx response."""

    code = "PORTAL_UNAVAILABLE"
    retryable = True


class PortalContractChanged(PortalError):
    """A selector, form field or JSON schema no longer matches expectations."""

    code = "PORTAL_CONTRACT_CHANGED"


class PortalCaptchaRequired(PortalError):
    """The portal asked for a CAPTCHA or a second factor; a human must intervene."""

    code = "PORTAL_CAPTCHA_REQUIRED"


class NoAvailability(PortalError):
    """The portal returned a valid but empty availability payload."""

    code = "NO_AVAILABILITY"


class ServiceNotBookableOnline(PortalError):
    """The portal offers the service, but not for booking online.

    Such a service is returned by autocomplete and can be selected, yet the
    step that follows exposes no location: the portal expects the appointment
    to be arranged by phone or at the counter.
    """

    code = "SERVICE_NOT_BOOKABLE_ONLINE"


class DoctorServiceRequired(PortalError):
    """The chosen doctor performs more than one service.

    The portal will not quote availability for a doctor alone: it wants the
    service too, and only the person searching can say which one.
    """

    code = "DOCTOR_SERVICE_REQUIRED"

    def __init__(
        self, detail: str = "", services: tuple[tuple[str, str], ...] = (), **context: Any
    ) -> None:
        super().__init__(detail, **context)
        #: ``(service_id, description)`` for each service the doctor performs.
        self.services = services


class SlotNoLongerAvailable(PortalError):
    code = "SLOT_NO_LONGER_AVAILABLE"


class BookingAmbiguous(PortalError):
    """The confirmation was sent but the outcome could not be reconciled."""

    code = "BOOKING_AMBIGUOUS"


class BookingRejected(PortalError):
    """The portal explicitly refused the booking."""

    code = "BOOKING_REJECTED"


class CancellationAmbiguous(PortalError):
    code = "CANCELLATION_AMBIGUOUS"


class PdfUnavailable(PortalError):
    code = "PDF_UNAVAILABLE"


class AmbiguousSearchResult(PortalError):
    """A stored search no longer maps to exactly one portal result."""

    code = "AMBIGUOUS_SEARCH_RESULT"


# --- Application -------------------------------------------------------------


class ApplicationError(DomainError):
    code = "APPLICATION_ERROR"


class NotFoundError(ApplicationError):
    code = "NOT_FOUND"


class PermissionDeniedError(ApplicationError):
    code = "PERMISSION_DENIED"


class ValidationError(ApplicationError):
    code = "VALIDATION_ERROR"


class ConflictError(ApplicationError):
    code = "CONFLICT"


class OptimisticLockError(ConflictError):
    code = "OPTIMISTIC_LOCK"


class LockUnavailableError(ApplicationError):
    """Another operation already holds the logical lock for this account/target."""

    code = "LOCK_UNAVAILABLE"


class RateLimitedError(ApplicationError):
    code = "RATE_LIMITED"

    def __init__(self, detail: str = "", retry_after_seconds: float = 0.0, **context: Any) -> None:
        super().__init__(detail, **context)
        self.retry_after_seconds = retry_after_seconds


class CircuitOpenError(ApplicationError):
    code = "CIRCUIT_OPEN"

    def __init__(self, detail: str = "", retry_after_seconds: float = 0.0, **context: Any) -> None:
        super().__init__(detail, **context)
        self.retry_after_seconds = retry_after_seconds


class ExpiredError(ApplicationError):
    code = "EXPIRED"


class AlreadyConsumedError(ApplicationError):
    code = "ALREADY_CONSUMED"


class QuotaExceededError(ApplicationError):
    code = "QUOTA_EXCEEDED"


class DecryptionError(ApplicationError):
    code = "DECRYPTION_FAILED"


class ConsentRequiredError(ApplicationError):
    code = "CONSENT_REQUIRED"


class AccountSuspendedError(ApplicationError):
    code = "ACCOUNT_SUSPENDED"
