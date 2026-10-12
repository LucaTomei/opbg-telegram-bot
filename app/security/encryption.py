"""Authenticated encryption for data at rest.

Every ciphertext is bound to the table, record identifier, field name and key
version through AES-GCM associated data, so a value cannot be silently moved
between records, fields or tenants.  Multiple key versions may be loaded at the
same time to allow online key rotation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.domain.errors import DecryptionError

ENVELOPE_VERSION: Final[int] = 1
NONCE_BYTES: Final[int] = 12
KEY_BYTES: Final[int] = 32
_HEADER_BYTES: Final[int] = 3  # envelope version + 2 bytes of key version
_HKDF_SALT: Final[bytes] = b"opbg-telegram-bot/encryption/v1"


def derive_key(master: bytes, *, key_version: int) -> bytes:
    """Derive the AES-256 key used for a given key version."""
    return HKDF(
        algorithm=hashes.SHA256(),
        length=KEY_BYTES,
        salt=_HKDF_SALT,
        info=f"field-encryption/key-version={key_version}".encode(),
    ).derive(master)


def build_associated_data(table: str, record_id: str, field: str, key_version: int) -> bytes:
    """Build the AES-GCM associated data binding a ciphertext to its location."""
    if not table or not field:
        raise ValueError("table and field are required to build associated data")
    return f"{table}|{record_id}|{field}|v{key_version}".encode()


@dataclass(frozen=True, slots=True)
class EncryptionContext:
    """Identifies where a ciphertext lives."""

    table: str
    record_id: str
    field: str


class EncryptionService:
    """AES-256-GCM field encryption with versioned keys."""

    def __init__(
        self,
        *,
        active_key: bytes,
        active_key_version: int = 1,
        previous_keys: dict[int, bytes] | None = None,
    ) -> None:
        if len(active_key) < KEY_BYTES:
            raise ValueError("encryption master key must provide at least 32 bytes")
        self._active_version = active_key_version
        self._ciphers: dict[int, AESGCM] = {
            active_key_version: AESGCM(derive_key(active_key, key_version=active_key_version))
        }
        for version, material in (previous_keys or {}).items():
            if version == active_key_version:
                raise ValueError("previous keys must not redeclare the active key version")
            if len(material) < KEY_BYTES:
                raise ValueError(f"previous key v{version} must provide at least 32 bytes")
            self._ciphers[version] = AESGCM(derive_key(material, key_version=version))

    @property
    def active_key_version(self) -> int:
        return self._active_version

    def encrypt(self, plaintext: str | bytes, context: EncryptionContext) -> bytes:
        """Encrypt a value, returning the self-describing envelope."""
        raw = plaintext.encode("utf-8") if isinstance(plaintext, str) else bytes(plaintext)
        nonce = os.urandom(NONCE_BYTES)
        aad = build_associated_data(
            context.table, context.record_id, context.field, self._active_version
        )
        ciphertext = self._ciphers[self._active_version].encrypt(nonce, raw, aad)
        header = bytes([ENVELOPE_VERSION]) + self._active_version.to_bytes(2, "big")
        return header + nonce + ciphertext

    def decrypt(self, envelope: bytes, context: EncryptionContext) -> bytes:
        """Decrypt an envelope, verifying the associated data."""
        if len(envelope) < _HEADER_BYTES + NONCE_BYTES + 16:
            raise DecryptionError("ciphertext envelope is truncated")
        if envelope[0] != ENVELOPE_VERSION:
            raise DecryptionError(f"unsupported envelope version {envelope[0]}")
        key_version = int.from_bytes(envelope[1:3], "big")
        cipher = self._ciphers.get(key_version)
        if cipher is None:
            raise DecryptionError(f"encryption key version {key_version} is not loaded")
        nonce = envelope[_HEADER_BYTES : _HEADER_BYTES + NONCE_BYTES]
        payload = envelope[_HEADER_BYTES + NONCE_BYTES :]
        aad = build_associated_data(context.table, context.record_id, context.field, key_version)
        try:
            return cipher.decrypt(nonce, payload, aad)
        except InvalidTag as exc:
            raise DecryptionError("authentication tag verification failed") from exc

    def decrypt_text(self, envelope: bytes, context: EncryptionContext) -> str:
        return self.decrypt(envelope, context).decode("utf-8")

    @staticmethod
    def key_version_of(envelope: bytes) -> int:
        """Return the key version recorded inside an envelope."""
        if len(envelope) < _HEADER_BYTES:
            raise DecryptionError("ciphertext envelope is truncated")
        return int.from_bytes(envelope[1:3], "big")

    def needs_rotation(self, envelope: bytes) -> bool:
        return self.key_version_of(envelope) != self._active_version

    def rewrap(self, envelope: bytes, context: EncryptionContext) -> bytes:
        """Re-encrypt an envelope under the active key version."""
        return self.encrypt(self.decrypt(envelope, context), context)


def generate_key_material() -> bytes:
    """Generate fresh master key material for a new key version."""
    return os.urandom(KEY_BYTES)
