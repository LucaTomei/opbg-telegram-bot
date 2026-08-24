"""Redaction must never let a secret reach a log record or a persisted event."""

from __future__ import annotations

import logging

import pytest

from app.logging import RedactingJsonFormatter, correlation_scope
from app.security.redaction import (
    REDACTED,
    mask_email,
    mask_generic,
    mask_person_name,
    mask_tax_code,
    redact_headers,
    redact_mapping,
    redact_text,
    redact_value,
)

SYNTHETIC_TAX_CODE = "BNCMRA80A01H501U"
SYNTHETIC_EMAIL = "mario.bianchi@example.invalid"


@pytest.mark.parametrize(
    "value",
    [
        SYNTHETIC_EMAIL,
        f"utente {SYNTHETIC_EMAIL} loggato",
        f"contact: {SYNTHETIC_EMAIL}",
    ],
)
def test_emails_are_redacted(value: str) -> None:
    assert SYNTHETIC_EMAIL not in redact_text(value)
    assert REDACTED in redact_text(value)


def test_tax_codes_are_redacted() -> None:
    assert SYNTHETIC_TAX_CODE not in redact_text(f"tax_code={SYNTHETIC_TAX_CODE}")


def test_query_string_tax_code_is_redacted() -> None:
    redacted = redact_text(f"/appointments/9/cancel?tax_code={SYNTHETIC_TAX_CODE}")
    assert SYNTHETIC_TAX_CODE not in redacted
    assert "tax_code=" in redacted


def test_bearer_and_jwt_tokens_are_redacted() -> None:
    payload = "Bearer abc.def-ghi and eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    redacted = redact_text(payload)
    assert "abc.def-ghi" not in redacted
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in redacted


def test_session_cookies_are_redacted() -> None:
    redacted = redact_text("opbg-prenotazione=abcdef123; path=/")
    assert "abcdef123" not in redacted
    assert "opbg-prenotazione=" in redacted


def test_html_bodies_are_never_logged() -> None:
    assert redact_text("<html><body>segreto</body></html>") == "[REDACTED_HTML]"
    assert redact_text('<form action="/login"><input name="password"></form>') == "[REDACTED_HTML]"


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "Password",
        "_token",
        "Cookie",
        "Set-Cookie",
        "csrf_token",
        "access_token",
        "refresh_token",
        "tax_code",
        "email",
        "X-CSRF-TOKEN",
        "session",
    ],
)
def test_sensitive_keys_are_dropped(key: str) -> None:
    assert redact_mapping({key: "supersecretvalue"})[key] == REDACTED


def test_nested_structures_are_redacted() -> None:
    payload = {
        "user": {"email": SYNTHETIC_EMAIL, "id": 4},
        "headers": [{"Authorization": "Bearer xyz"}],
        "note": f"paziente {SYNTHETIC_TAX_CODE}",
    }
    redacted = redact_mapping(payload)
    assert redacted["user"]["email"] == REDACTED
    assert redacted["user"]["id"] == 4
    assert redacted["headers"][0]["Authorization"] == REDACTED
    assert SYNTHETIC_TAX_CODE not in redacted["note"]


def test_bytes_are_never_serialised() -> None:
    assert redact_value(b"\x00\x01\x02") == "[BYTES:3]"


def test_headers_are_redacted() -> None:
    headers = redact_headers({"Cookie": "a=b", "Accept": "text/html"})
    assert headers["Cookie"] == REDACTED
    assert headers["Accept"] == "text/html"


def test_long_text_is_truncated() -> None:
    assert redact_text("x" * 900).endswith("…[TRUNCATED]")


def test_masking_helpers_keep_no_full_value() -> None:
    assert mask_email(SYNTHETIC_EMAIL).startswith("m")
    assert SYNTHETIC_EMAIL not in mask_email(SYNTHETIC_EMAIL)
    assert mask_tax_code(SYNTHETIC_TAX_CODE) != SYNTHETIC_TAX_CODE
    assert mask_tax_code(SYNTHETIC_TAX_CODE).startswith("BNC")
    assert mask_person_name("Mario Bianchi") == "M. B."
    assert mask_generic("abcdef") == "ab****"


def test_log_formatter_redacts_message_and_extras() -> None:
    formatter = RedactingJsonFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="login %s",
        args=(SYNTHETIC_EMAIL,),
        exc_info=None,
    )
    record.password = "hunter2"
    record.event_code = "LOGIN"
    with correlation_scope("abc123"):
        rendered = formatter.format(record)
    assert SYNTHETIC_EMAIL not in rendered
    assert "hunter2" not in rendered
    assert '"event_code":"LOGIN"' in rendered
    assert '"correlation_id":"abc123"' in rendered
