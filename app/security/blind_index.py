"""Keyed blind indexes for equality lookups over encrypted columns.

A blind index is an HMAC-SHA256 over a normalised value, computed with a key
that is derived independently from the encryption key.  It never allows the
plaintext to be recovered and it is domain-separated per table and field so the
same value in two tables produces two unrelated indexes.
"""

from __future__ import annotations

import hmac
import unicodedata
from hashlib import sha256
from typing import Final

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

BLIND_INDEX_BYTES: Final[int] = 32
_HKDF_SALT: Final[bytes] = b"opbg-telegram-bot/blind-index/v1"


def normalize_lookup_value(value: str) -> str:
    """Normalise a value so that equivalent inputs share the same index."""
    return unicodedata.normalize("NFKC", value).strip().casefold()


class BlindIndexService:
    """Derive deterministic, domain-separated lookup indexes."""

    def __init__(self, *, master_key: bytes) -> None:
        if len(master_key) < 32:
            raise ValueError("blind index master key must provide at least 32 bytes")
        self._master = master_key
        self._cache: dict[str, bytes] = {}

    def _domain_key(self, table: str, field: str) -> bytes:
        cache_key = f"{table}|{field}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        derived = HKDF(
            algorithm=hashes.SHA256(),
            length=BLIND_INDEX_BYTES,
            salt=_HKDF_SALT,
            info=cache_key.encode("utf-8"),
        ).derive(self._master)
        self._cache[cache_key] = derived
        return derived

    def compute(self, value: str, *, table: str, field: str) -> bytes:
        """Return the blind index for ``value`` inside ``table.field``."""
        if not table or not field:
            raise ValueError("table and field are required for a blind index")
        normalized = normalize_lookup_value(value)
        if not normalized:
            raise ValueError("cannot compute a blind index for an empty value")
        return hmac.new(self._domain_key(table, field), normalized.encode("utf-8"), sha256).digest()

    def matches(self, value: str, index: bytes, *, table: str, field: str) -> bool:
        return hmac.compare_digest(self.compute(value, table=table, field=field), index)
