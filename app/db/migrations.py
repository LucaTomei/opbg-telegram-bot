"""Helpers shared by the Alembic environment and the migration tests."""

from __future__ import annotations

from typing import Any

from sqlalchemy.sql.sqltypes import _Binary
from sqlalchemy.types import TypeEngine

from app.db.types import Ciphertext


def compare_type(
    context: Any,
    inspected_column: Any,
    metadata_column: Any,
    inspected_type: TypeEngine[Any],
    metadata_type: TypeEngine[Any],
) -> bool | None:
    """Treat every BLOB variant as equivalent to :class:`Ciphertext`.

    MySQL maps a ``LargeBinary`` length to TINYBLOB/BLOB/MEDIUMBLOB, so
    reflection never returns the declared type decorator.  Returning ``False``
    here avoids an endless "type changed" diff on encrypted columns.
    """
    if isinstance(metadata_type, Ciphertext) and isinstance(inspected_type, _Binary):
        return False
    return None
