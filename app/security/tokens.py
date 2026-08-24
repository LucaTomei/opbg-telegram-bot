"""One-time token generation, hashing and verification.

Invite codes, connect tickets, PDF download links and destructive-action nonces
are all high-entropy random values.  Only their SHA-256 hash is persisted, and
every comparison is constant time.
"""

from __future__ import annotations

import hmac
import secrets
from hashlib import sha256
from typing import Final

TOKEN_BYTES: Final[int] = 32
INVITE_CODE_BYTES: Final[int] = 12
NONCE_BYTES: Final[int] = 16
HASH_BYTES: Final[int] = 32

_INVITE_ALPHABET: Final[str] = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_token() -> str:
    """Generate a URL-safe one-time token."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def generate_nonce() -> str:
    """Generate a short-lived confirmation nonce."""
    return secrets.token_urlsafe(NONCE_BYTES)


def generate_invite_code() -> str:
    """Generate a human-typable, unambiguous invite code."""
    raw = "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(INVITE_CODE_BYTES))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def normalize_invite_code(code: str) -> str:
    """Normalise a user-typed invite code before hashing."""
    return "".join(ch for ch in code.strip().upper() if ch.isalnum())


def hash_token(token: str) -> bytes:
    """Return the SHA-256 digest persisted in place of the token."""
    return sha256(token.encode("utf-8")).digest()


def hash_invite_code(code: str) -> bytes:
    """Hash an invite code after normalisation."""
    normalized = normalize_invite_code(code)
    if not normalized:
        raise ValueError("invite code must not be empty")
    return sha256(normalized.encode("utf-8")).digest()


def verify_token(token: str, token_hash: bytes) -> bool:
    return hmac.compare_digest(hash_token(token), token_hash)


def verify_invite_code(code: str, code_hash: bytes) -> bool:
    try:
        return hmac.compare_digest(hash_invite_code(code), code_hash)
    except ValueError:
        return False


def hash_context(*parts: str) -> bytes:
    """Hash the immutable context a confirmation nonce is bound to."""
    digest = sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x1f")
    return digest.digest()


def content_hash(payload: str) -> str:
    """Return a hex digest used in audit ``before``/``after`` fields."""
    return sha256(payload.encode("utf-8")).hexdigest()
