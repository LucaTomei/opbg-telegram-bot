"""Shared repository helpers.

Repositories own all SQL: handlers, services and the portal adapter never build
queries themselves.  They also translate SQLAlchemy integrity and staleness
errors into typed domain errors.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, Executable, Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.db.base import Base
from app.domain.errors import ConflictError, OptimisticLockError


def utc_now() -> datetime:
    """Naive UTC timestamp matching the storage convention."""
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


@dataclass(frozen=True, slots=True)
class Page[ModelT: Base]:
    """One page of results plus the information needed to render a pager."""

    items: Sequence[ModelT]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        if self.page_size <= 0:
            return 1
        return max(1, -(-self.total // self.page_size))

    @property
    def has_previous(self) -> bool:
        return self.page > 0

    @property
    def has_next(self) -> bool:
        return self.page + 1 < self.pages


class BaseRepository[ModelT: Base]:
    """Common query helpers bound to a single session."""

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, entity_id: Any) -> ModelT | None:
        return await self.session.get(self.model, entity_id)

    async def one_or_none(self, statement: Select[Any]) -> ModelT | None:
        """Typed wrapper around ``session.scalar`` for this repository's model."""
        return cast("ModelT | None", await self.session.scalar(statement))

    async def execute_dml(self, statement: Executable) -> int:
        """Run a bulk ``DELETE``/``UPDATE`` and return the affected row count."""
        result = cast(CursorResult[Any], await self.session.execute(statement))
        await self.flush()
        return int(result.rowcount or 0)

    async def add(self, entity: ModelT) -> ModelT:
        self.session.add(entity)
        await self.flush()
        return entity

    async def flush(self) -> None:
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise ConflictError("database constraint violated") from exc
        except StaleDataError as exc:
            raise OptimisticLockError("record was modified concurrently") from exc

    async def count(self, statement: Select[Any]) -> int:
        subquery = statement.order_by(None).subquery()
        total = await self.session.scalar(select(func.count()).select_from(subquery))
        return int(total or 0)

    async def paginate(self, statement: Select[Any], *, page: int, page_size: int) -> Page[ModelT]:
        """Return a page of results together with the total row count."""
        safe_page = max(page, 0)
        safe_size = max(page_size, 1)
        total = await self.count(statement)
        result = await self.session.scalars(
            statement.limit(safe_size).offset(safe_page * safe_size)
        )
        return Page(items=list(result), total=total, page=safe_page, page_size=safe_size)
