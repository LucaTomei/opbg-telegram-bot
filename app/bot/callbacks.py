"""Compact callback payloads.

Telegram limits callback data to 64 bytes, so nothing but a namespace, an
action and opaque identifiers ever travels in a button.  Identifiers are the
16 raw bytes of a UUID encoded in URL-safe base64 (22 characters); everything
else — filters, slot details, drafts — lives server side and is reloaded and
re-authorised on every callback.
"""

from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass
from typing import Final

SEPARATOR: Final[str] = "|"
MAX_CALLBACK_BYTES: Final[int] = 64


class CallbackTooLongError(ValueError):
    """Raised when a payload would exceed the Telegram limit."""


class InvalidCallbackError(ValueError):
    """Raised when an incoming payload cannot be parsed."""


def pack_uuid(value: uuid.UUID) -> str:
    """Encode a UUID as 22 URL-safe base64 characters."""
    return base64.urlsafe_b64encode(value.bytes).decode("ascii").rstrip("=")


def unpack_uuid(value: str) -> uuid.UUID:
    """Decode an identifier produced by :func:`pack_uuid`."""
    padded = value + "=" * (-len(value) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise InvalidCallbackError("malformed identifier") from exc
    if len(raw) != 16:
        raise InvalidCallbackError("malformed identifier")
    return uuid.UUID(bytes=raw)


@dataclass(frozen=True, slots=True)
class Callback:
    """A parsed callback payload."""

    namespace: str
    action: str
    args: tuple[str, ...] = ()

    def arg(self, index: int, default: str = "") -> str:
        return self.args[index] if index < len(self.args) else default

    def uuid_arg(self, index: int) -> uuid.UUID:
        return unpack_uuid(self.arg(index))

    def int_arg(self, index: int, default: int = 0) -> int:
        raw = self.arg(index)
        try:
            return int(raw)
        except ValueError:
            return default

    def encode(self) -> str:
        return build(self.namespace, self.action, *self.args)


def build(namespace: str, action: str, *args: str | int | uuid.UUID) -> str:
    """Build a callback payload, failing loudly if it would be truncated."""
    parts: list[str] = [namespace, action]
    for item in args:
        if isinstance(item, uuid.UUID):
            parts.append(pack_uuid(item))
        else:
            parts.append(str(item))
    payload = SEPARATOR.join(parts)
    if len(payload.encode("utf-8")) > MAX_CALLBACK_BYTES:
        raise CallbackTooLongError(f"callback payload is too long: {len(payload)} characters")
    return payload


def parse(payload: str | None) -> Callback:
    """Parse an incoming callback payload."""
    if not payload:
        raise InvalidCallbackError("empty callback payload")
    parts = payload.split(SEPARATOR)
    if len(parts) < 2:
        raise InvalidCallbackError("callback payload has no action")
    return Callback(namespace=parts[0], action=parts[1], args=tuple(parts[2:]))


# --- namespaces --------------------------------------------------------------
NAV: Final[str] = "nv"
ACCOUNT: Final[str] = "ac"
BOOKING: Final[str] = "bk"
FILTERS: Final[str] = "fl"
WATCH: Final[str] = "wt"
APPOINTMENT: Final[str] = "ap"
SETTINGS: Final[str] = "st"
ADMIN: Final[str] = "ad"
CONSENT: Final[str] = "cs"

# --- shared actions ----------------------------------------------------------
ACTION_MENU: Final[str] = "menu"
ACTION_CANCEL: Final[str] = "cancel"
ACTION_NOOP: Final[str] = "noop"


def menu() -> str:
    return build(NAV, ACTION_MENU)


def cancel() -> str:
    return build(NAV, ACTION_CANCEL)


def noop() -> str:
    return build(NAV, ACTION_NOOP)


def page(namespace: str, action: str, page_number: int, *args: str | int | uuid.UUID) -> str:
    return build(namespace, action, page_number, *args)
