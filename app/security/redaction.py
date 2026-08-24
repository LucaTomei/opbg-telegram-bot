"""Central redaction helpers.

Every value that leaves the process through a log record, a system event, a
persisted error detail or an admin screen must pass through this module.  The
rules are intentionally conservative: it is better to redact a harmless value
than to leak a credential, a cookie, a CSRF token or a tax code.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final

REDACTED: Final[str] = "[REDACTED]"

#: Keys whose value must never appear in a log record or persisted payload.
SENSITIVE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "_token",
        "access_token",
        "authorization",
        "blind_index_key",
        "codice_fiscale",
        "confirm",
        "cookie",
        "cookie_jar",
        "cookies",
        "csrf",
        "csrf_token",
        "email",
        "email_address",
        "encryption_key",
        "first_name",
        "html",
        "id_token",
        "last_name",
        "mail",
        "nonce",
        "password",
        "passwd",
        "patient_name",
        "pwd",
        "raw",
        "raw_body",
        "raw_html",
        "raw_response",
        "refresh_token",
        "secret",
        "session",
        "session_cookie",
        "set-cookie",
        "tax_code",
        "telegram_bot_token",
        "ticket",
        "token",
        "username",
        "x-csrf-token",
    }
)

#: Header names that are removed wholesale from any structured log payload.
SENSITIVE_HEADERS: Final[frozenset[str]] = frozenset(
    {"authorization", "cookie", "set-cookie", "x-csrf-token", "x-xsrf-token", "proxy-authorization"}
)

_EMAIL_RE: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
)
_TAX_CODE_RE: Final[re.Pattern[str]] = re.compile(
    r"\b[A-Za-z]{6}\d{2}[A-Za-z]\d{2}[A-Za-z]\d{3}[A-Za-z]\b",
)
_BEARER_RE: Final[re.Pattern[str]] = re.compile(r"\bBearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE)
_JWT_RE: Final[re.Pattern[str]] = re.compile(r"\beyJ[A-Za-z0-9._\-]{10,}")
_COOKIE_PAIR_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)\b(opbg-prenotazione|xsrf-token|laravel_session|session)\s*=\s*[^;\s]+",
)
_QUERY_TAX_CODE_RE: Final[re.Pattern[str]] = re.compile(r"(?i)([?&]tax_code=)[^&\s]+")
_LONG_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"\b[A-Fa-f0-9]{32,}\b")
_HTML_RE: Final[re.Pattern[str]] = re.compile(
    r"<\s*(!doctype|html|head|body|form|div|table)\b", re.IGNORECASE
)

_MAX_TEXT_LENGTH: Final[int] = 512
_MAX_DEPTH: Final[int] = 6


def redact_text(value: str) -> str:
    """Redact secrets embedded in a free-form string."""
    if _HTML_RE.search(value):
        return "[REDACTED_HTML]"
    redacted = _BEARER_RE.sub(REDACTED, value)
    redacted = _JWT_RE.sub(REDACTED, redacted)
    redacted = _COOKIE_PAIR_RE.sub(lambda m: f"{m.group(1)}={REDACTED}", redacted)
    redacted = _QUERY_TAX_CODE_RE.sub(lambda m: f"{m.group(1)}{REDACTED}", redacted)
    redacted = _EMAIL_RE.sub(REDACTED, redacted)
    redacted = _TAX_CODE_RE.sub(REDACTED, redacted)
    redacted = _LONG_TOKEN_RE.sub(REDACTED, redacted)
    if len(redacted) > _MAX_TEXT_LENGTH:
        redacted = redacted[:_MAX_TEXT_LENGTH] + "…[TRUNCATED]"
    return redacted


def is_sensitive_key(key: str) -> bool:
    """Return ``True`` when a mapping key must never expose its value."""
    normalized = key.strip().lower().replace("-", "_")
    if normalized in SENSITIVE_KEYS:
        return True
    return any(marker in normalized for marker in ("password", "token", "cookie", "secret", "csrf"))


def redact_value(value: object, *, _depth: int = 0) -> Any:
    """Recursively redact an arbitrary structure for structured logging."""
    if _depth > _MAX_DEPTH:
        return "[TRUNCATED_DEPTH]"
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return f"[BYTES:{len(bytes(value))}]"
    if isinstance(value, Mapping):
        return {
            str(key): (
                REDACTED if is_sensitive_key(str(key)) else redact_value(item, _depth=_depth + 1)
            )
            for key, item in value.items()
        }
    if isinstance(value, Sequence):
        return [redact_value(item, _depth=_depth + 1) for item in value]
    if isinstance(value, Iterable):
        return [redact_value(item, _depth=_depth + 1) for item in list(value)[:20]]
    return redact_text(repr(value))


def redact_mapping(payload: Mapping[str, object]) -> dict[str, Any]:
    """Redact a flat or nested mapping, preserving key order."""
    result = redact_value(payload)
    if not isinstance(result, dict):  # pragma: no cover - redact_value contract
        raise TypeError("redact_value must return a dict for mapping input")
    return result


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Redact HTTP headers, dropping the value of every sensitive header."""
    return {
        name: (REDACTED if name.strip().lower() in SENSITIVE_HEADERS else redact_text(value))
        for name, value in headers.items()
    }


def mask_email(email: str) -> str:
    """Return a display-safe masked representation of an e-mail address."""
    local, _, domain = email.partition("@")
    if not domain:
        return mask_generic(email)
    visible = local[:1] if local else ""
    domain_parts = domain.split(".")
    masked_domain = "*" * max(len(domain_parts[0]), 1)
    suffix = "." + ".".join(domain_parts[1:]) if len(domain_parts) > 1 else ""
    return f"{visible}{'*' * max(len(local) - 1, 1)}@{masked_domain}{suffix}"


def mask_tax_code(tax_code: str) -> str:
    """Return a display-safe masked representation of an Italian tax code."""
    cleaned = tax_code.strip()
    if len(cleaned) <= 4:
        return "*" * len(cleaned)
    return f"{cleaned[:3]}{'*' * (len(cleaned) - 5)}{cleaned[-2:]}"


def mask_generic(value: str, *, keep: int = 2) -> str:
    """Mask an arbitrary identifier keeping only a short prefix."""
    cleaned = value.strip()
    if len(cleaned) <= keep:
        return "*" * len(cleaned)
    return f"{cleaned[:keep]}{'*' * (len(cleaned) - keep)}"


def mask_person_name(name: str) -> str:
    """Mask a person name keeping initials only."""
    parts = [part for part in re.split(r"\s+", name.strip()) if part]
    if not parts:
        return ""
    return " ".join(f"{part[0].upper()}." for part in parts)
