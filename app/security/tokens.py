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

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

TOKEN_BYTES: Final[int] = 32
INVITE_CODE_BYTES: Final[int] = 12
NONCE_BYTES: Final[int] = 16
HASH_BYTES: Final[int] = 32

_INVITE_ALPHABET: Final[str] = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_PEPPER_SALT: Final[bytes] = b"opbg-telegram-bot/token-pepper/v1"


class TokenHasher:
    """Hashes one-time tokens with a server side pepper.

    Only the digest is persisted.  Deriving it with a keyed HMAC means that a
    read-only leak of the database is not enough to verify a guessed invite
    code or download token: the application secret is required as well.
    """

    def __init__(self, *, secret: bytes) -> None:
        if len(secret) < 32:
            raise ValueError("the token pepper must provide at least 32 bytes")
        self._pepper = HKDF(
            algorithm=hashes.SHA256(),
            length=HASH_BYTES,
            salt=_PEPPER_SALT,
            info=b"one-time-tokens",
        ).derive(secret)

    def hash(self, value: str) -> bytes:
        return hmac.new(self._pepper, value.encode("utf-8"), sha256).digest()

    def verify(self, value: str, digest: bytes) -> bool:
        return hmac.compare_digest(self.hash(value), digest)


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
    """Return the peppered digest persisted in place of the token."""
    from app.security.context import get_token_hasher

    return get_token_hasher().hash(token)


def hash_invite_code(code: str) -> bytes:
    """Hash an invite code after normalisation."""
    from app.security.context import get_token_hasher

    normalized = normalize_invite_code(code)
    if not normalized:
        raise ValueError("invite code must not be empty")
    return get_token_hasher().hash(normalized)


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
