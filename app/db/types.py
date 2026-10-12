"""Custom SQLAlchemy column types and identifier helpers."""

from __future__ import annotations

import os
import time
import uuid
from typing import Any, Final

import orjson
from sqlalchemy import BINARY, Dialect, LargeBinary, TypeDecorator
from sqlalchemy.dialects.mysql import JSON as MYSQL_JSON

UUID_BYTES: Final[int] = 16
BLIND_INDEX_COLUMN_BYTES: Final[int] = 32


def uuid7() -> uuid.UUID:
    """Generate a time-ordered UUIDv7.

    Time-ordered identifiers keep the clustered InnoDB primary key sequential,
    which avoids page splits on insert-heavy tables such as ``seen_slots``.
    """
    unix_ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF
    random_bytes = os.urandom(10)
    value = bytearray(unix_ms.to_bytes(6, "big") + random_bytes)
    value[6] = (value[6] & 0x0F) | 0x70  # version 7
    value[8] = (value[8] & 0x3F) | 0x80  # RFC 4122 variant
    return uuid.UUID(bytes=bytes(value))


class UuidBinary(TypeDecorator[uuid.UUID]):
    """Store a :class:`uuid.UUID` as a compact ``BINARY(16)`` column."""

    impl = BINARY(UUID_BYTES)
    cache_ok = True

    def process_bind_param(self, value: uuid.UUID | str | None, dialect: Dialect) -> bytes | None:
        if value is None:
            return None
        if isinstance(value, str):
            value = uuid.UUID(value)
        return value.bytes

    def process_result_value(self, value: bytes | None, dialect: Dialect) -> uuid.UUID | None:
        if value is None:
            return None
        return uuid.UUID(bytes=bytes(value))


class BlindIndexColumn(TypeDecorator[bytes]):
    """Fixed-size ``BINARY(32)`` column holding an HMAC blind index."""

    impl = BINARY(BLIND_INDEX_COLUMN_BYTES)
    cache_ok = True

    def process_bind_param(self, value: bytes | None, dialect: Dialect) -> bytes | None:
        if value is None:
            return None
        if len(value) != BLIND_INDEX_COLUMN_BYTES:
            raise ValueError("a blind index must be exactly 32 bytes long")
        return bytes(value)

    def process_result_value(self, value: bytes | None, dialect: Dialect) -> bytes | None:
        return None if value is None else bytes(value)


class Ciphertext(TypeDecorator[bytes]):
    """Opaque AES-GCM envelope stored as ``VARBINARY``."""

    impl = LargeBinary
    cache_ok = True

    def __init__(self, length: int = 4096) -> None:
        super().__init__(length=length)

    def process_bind_param(self, value: bytes | None, dialect: Dialect) -> bytes | None:
        return None if value is None else bytes(value)

    def process_result_value(self, value: bytes | None, dialect: Dialect) -> bytes | None:
        return None if value is None else bytes(value)


class JsonDocument(TypeDecorator[Any]):
    """JSON column serialised with ``orjson`` for deterministic output."""

    impl = MYSQL_JSON
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode("utf-8")

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if isinstance(value, str | bytes | bytearray):
            return orjson.loads(value)
        return value
