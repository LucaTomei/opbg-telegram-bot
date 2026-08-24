"""Process-wide security services used by ORM field descriptors.

Encryption and blind-index services are configured once by each composition
root (``main_bot``, ``main_worker``, Alembic and the test suite).
ORM attribute descriptors cannot receive constructor arguments, so they resolve
the services through this module; every other layer receives them by explicit
dependency injection.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from app.config import Settings
from app.security.blind_index import BlindIndexService
from app.security.encryption import EncryptionService
from app.security.tokens import TokenHasher

_encryption: EncryptionService | None = None
_blind_index: BlindIndexService | None = None
_token_hasher: TokenHasher | None = None


class SecurityNotConfiguredError(RuntimeError):
    """Raised when encrypted data is accessed before security is configured."""


def configure_security(
    encryption: EncryptionService,
    blind_index: BlindIndexService,
    token_hasher: TokenHasher | None = None,
) -> None:
    """Install the process-wide security services."""
    global _encryption, _blind_index, _token_hasher
    _encryption = encryption
    _blind_index = blind_index
    _token_hasher = token_hasher


def configure_security_from_settings(settings: Settings) -> None:
    """Build both services from validated settings and install them."""
    configure_security(
        EncryptionService(
            active_key=settings.active_encryption_key,
            active_key_version=settings.app_encryption_key_version,
            previous_keys=settings.previous_encryption_keys,
        ),
        BlindIndexService(master_key=settings.blind_index_key),
        TokenHasher(secret=settings.token_pepper),
    )


def get_encryption() -> EncryptionService:
    if _encryption is None:
        raise SecurityNotConfiguredError("encryption service is not configured")
    return _encryption


def get_blind_index() -> BlindIndexService:
    if _blind_index is None:
        raise SecurityNotConfiguredError("blind index service is not configured")
    return _blind_index


def get_token_hasher() -> TokenHasher:
    if _token_hasher is None:
        raise SecurityNotConfiguredError("token hasher is not configured")
    return _token_hasher


def is_configured() -> bool:
    return _encryption is not None and _blind_index is not None and _token_hasher is not None


@contextmanager
def security_context(
    encryption: EncryptionService,
    blind_index: BlindIndexService,
    token_hasher: TokenHasher | None = None,
) -> Iterator[None]:
    """Temporarily install security services; used by tests and rotation jobs."""
    global _encryption, _blind_index, _token_hasher
    previous = (_encryption, _blind_index, _token_hasher)
    configure_security(encryption, blind_index, token_hasher)
    try:
        yield
    finally:
        _encryption, _blind_index, _token_hasher = previous
