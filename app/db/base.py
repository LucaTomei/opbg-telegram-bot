"""Declarative base, shared mixins and encrypted-attribute descriptors."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, ClassVar, cast

import orjson
from sqlalchemy import BigInteger, DateTime, MetaData, Table, event
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

from app.db.types import UuidBinary, uuid7
from app.security.context import get_blind_index, get_encryption
from app.security.encryption import EncryptionContext

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    """Return an aware UTC timestamp; the database stores naive UTC values."""
    return datetime.now(UTC).replace(microsecond=0)


def naive_utcnow() -> datetime:
    return utcnow().replace(tzinfo=None)


class Base(DeclarativeBase):
    """Declarative base with an explicit naming convention for migrations."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    #: Set to ``True`` by :class:`UuidPkMixin` so ids exist before flush.
    _auto_uuid_pk: ClassVar[bool] = False

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        identifier = getattr(self, "id", None)
        return f"<{type(self).__name__} id={identifier}>"


@event.listens_for(Base, "init", propagate=True)
def _assign_primary_key(target: Base, _args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
    """Generate the UUIDv7 primary key eagerly.

    Encrypted attributes bind their ciphertext to the record id through AES-GCM
    associated data, so the id must be known while the object is still transient.
    """
    if type(target)._auto_uuid_pk and not kwargs.get("id"):
        kwargs["id"] = uuid7()


class UuidPkMixin:
    """Binary UUIDv7 primary key."""

    _auto_uuid_pk: ClassVar[bool] = True

    id: Mapped[uuid.UUID] = mapped_column(UuidBinary, primary_key=True, default=uuid7)


class TimestampMixin:
    """``created_at`` / ``updated_at`` maintained by the application in UTC."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=naive_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=naive_utcnow, onupdate=naive_utcnow, nullable=False
    )


class SoftDeleteMixin:
    """Soft deletion marker for records that must survive for audit purposes."""

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), default=None)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class VersionMixin:
    """Optimistic locking counter managed by SQLAlchemy."""

    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:  # noqa: N805
        table = cast(Table, cls.__table__)  # type: ignore[attr-defined]
        return {"version_id_col": table.c.version}


class EncryptedAttribute[T]:
    """Descriptor exposing an encrypted column as a plaintext attribute.

    The plaintext never reaches the database: reading decrypts on demand and
    writing encrypts immediately, binding the ciphertext to
    ``(table, record id, field, key version)``.
    """

    def __init__(
        self,
        column_name: str,
        *,
        encoder: Callable[[T], str] | None = None,
        decoder: Callable[[str], T] | None = None,
    ) -> None:
        self._column_name = column_name
        self._encoder = encoder
        self._decoder = decoder
        self._field_name = column_name.removesuffix("_ciphertext")

    def __set_name__(self, owner: type[Any], name: str) -> None:
        self._public_name = name

    def _context(self, instance: Any) -> EncryptionContext:
        record_id = getattr(instance, "id", None)
        if record_id is None:
            raise ValueError(
                f"{type(instance).__name__}.id must be assigned before encrypting "
                f"{self._field_name}"
            )
        identifier = record_id.hex if isinstance(record_id, uuid.UUID) else str(record_id)
        return EncryptionContext(
            table=instance.__tablename__, record_id=identifier, field=self._field_name
        )

    def __get__(self, instance: Any, owner: type[Any] | None = None) -> T | None:
        if instance is None:
            return cast(T, self)
        envelope = getattr(instance, self._column_name)
        if envelope is None:
            return None
        plaintext = get_encryption().decrypt_text(envelope, self._context(instance))
        return cast(T, self._decoder(plaintext) if self._decoder else plaintext)

    def __set__(self, instance: Any, value: T | None) -> None:
        if value is None:
            setattr(instance, self._column_name, None)
            return
        plaintext = self._encoder(value) if self._encoder else cast(str, value)
        envelope = get_encryption().encrypt(plaintext, self._context(instance))
        setattr(instance, self._column_name, envelope)


def encrypted_text(column_name: str) -> EncryptedAttribute[str]:
    """Expose an encrypted text column as a plaintext ``str`` attribute."""
    return EncryptedAttribute[str](column_name)


def encrypted_json(column_name: str) -> EncryptedAttribute[Any]:
    """Expose an encrypted column holding a JSON document."""
    return EncryptedAttribute[Any](
        column_name,
        encoder=lambda value: orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode("utf-8"),
        decoder=orjson.loads,
    )


def blind_index_for(table: str, field: str, value: str) -> bytes:
    """Compute the blind index used to look up an encrypted column."""
    return get_blind_index().compute(value, table=table, field=field)
