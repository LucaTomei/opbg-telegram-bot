"""User-facing error message unit tests."""

from __future__ import annotations

import pytest

from app.bot.presenters.errors import describe_error
from app.domain import errors
from app.security.redaction import REDACTED

SYNTHETIC_EMAIL = "utente@example.invalid"


@pytest.mark.parametrize(
    "error",
    [
        errors.AuthenticationFailed("wrong password"),
        errors.ReauthenticationRequired("session expired"),
        errors.PortalRateLimited("429"),
        errors.PortalUnavailable("timeout"),
        errors.PortalContractChanged("selector changed"),
        errors.PortalCaptchaRequired("recaptcha"),
        errors.NoAvailability("empty"),
        errors.SlotNoLongerAvailable("gone"),
        errors.BookingAmbiguous("unclear"),
        errors.BookingRejected("refused"),
        errors.CancellationAmbiguous("unclear"),
        errors.PdfUnavailable("missing"),
        errors.AmbiguousSearchResult("two matches"),
        errors.NotFoundError("missing"),
        errors.PermissionDeniedError("denied"),
        errors.ValidationError("bad input"),
        errors.ConflictError("duplicate"),
        errors.OptimisticLockError("stale"),
        errors.LockUnavailableError("busy"),
        errors.RateLimitedError("slow down"),
        errors.CircuitOpenError("open"),
        errors.ExpiredError("expired"),
        errors.AlreadyConsumedError("used"),
        errors.QuotaExceededError("too many"),
        errors.DecryptionError("tampered"),
        errors.ConsentRequiredError("consent"),
        errors.AccountSuspendedError("suspended"),
    ],
)
def test_every_domain_error_has_an_italian_message(error: errors.DomainError) -> None:
    message = describe_error(error, "cid123")
    assert message
    assert "Traceback" not in message
    assert error.code not in message or error.code in {"PORTAL_CONTRACT_CHANGED"}


def test_unknown_errors_fall_back_to_the_generic_message() -> None:
    class Unexpected(errors.DomainError):
        code = "UNEXPECTED"

    assert "cid123" in describe_error(Unexpected("boom"), "cid123")


def test_ambiguous_outcomes_expose_the_correlation_id() -> None:
    assert "cid123" in describe_error(errors.BookingAmbiguous("x"), "cid123")
    assert "cid123" in describe_error(errors.CancellationAmbiguous("x"), "cid123")
    assert "cid123" in describe_error(errors.PortalContractChanged("x"), "cid123")


def test_error_details_are_redacted_at_construction() -> None:
    error = errors.AuthenticationFailed(f"login failed for {SYNTHETIC_EMAIL}")
    assert SYNTHETIC_EMAIL not in error.detail
    assert REDACTED in error.detail


def test_error_context_is_redacted() -> None:
    error = errors.PortalUnavailable("boom", cookie="opbg-prenotazione=abcdef123456")
    assert "abcdef123456" not in "".join(error.context.values())


def test_html_is_never_kept_in_an_error_detail() -> None:
    error = errors.PortalContractChanged("<html><body>segreto</body></html>")
    assert "segreto" not in error.detail


def test_retryable_flags_match_the_documented_policy() -> None:
    assert errors.PortalUnavailable().retryable is True
    assert errors.PortalRateLimited().retryable is True
    assert errors.BookingAmbiguous().retryable is False
    assert errors.AuthenticationFailed().retryable is False
