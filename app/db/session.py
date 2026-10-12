"""Async engine, session factory and MySQL-based locking primitives."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings
from app.domain.errors import LockUnavailableError
from app.logging import get_logger

logger = get_logger(__name__)

#: MySQL named locks are limited to 64 characters.
MAX_LOCK_NAME_LENGTH = 60

#: The session that owns the current unit of work, when one is active.
_CURRENT_SESSION: ContextVar[AsyncSession | None] = ContextVar("current_session", default=None)


def current_session() -> AsyncSession | None:
    """Return the ambient session, if the caller runs inside a unit of work."""
    return _CURRENT_SESSION.get()


@asynccontextmanager
async def bind_session(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    """Publish ``session`` as the ambient unit of work for the current task.

    Components that cannot receive the session by injection — the aiogram FSM
    storage is resolved before the middleware chain — reuse it instead of
    opening a second transaction.  Two transactions on the same rows inside one
    update would otherwise block each other on the foreign-key parent locks.
    """
    token = _CURRENT_SESSION.set(session)
    try:
        yield session
    finally:
        _CURRENT_SESSION.reset(token)


def create_engine(settings: Settings) -> AsyncEngine:
    """Build the async engine with a pool sized for a small private deployment."""
    return create_async_engine(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_recycle=settings.database_pool_recycle_seconds,
        pool_pre_ping=True,
        future=True,
        echo=False,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
        autoflush=False,
        class_=AsyncSession,
    )


class Database:
    """Owns the engine and hands out sessions and named locks."""

    def __init__(self, settings: Settings, engine: AsyncEngine | None = None) -> None:
        self._settings = settings
        self._engine = engine or create_engine(settings)
        self._session_factory = create_session_factory(self._engine)

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self._session_factory

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a session that is committed on success and rolled back on error."""
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    @asynccontextmanager
    async def read_session(self) -> AsyncIterator[AsyncSession]:
        """Yield a read-only session that never commits."""
        async with self._session_factory() as session:
            try:
                yield session
            finally:
                await session.rollback()

    async def ping(self) -> bool:
        """Return ``True`` when the database answers a trivial query."""
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception:
            logger.warning("database ping failed", extra={"event_code": "DB_PING_FAILED"})
            return False
        return True

    async def current_schema_revision(self) -> str | None:
        """Return the Alembic revision currently applied to the database."""
        try:
            async with self._engine.connect() as connection:
                result = await connection.execute(text("SELECT version_num FROM alembic_version"))
                row = result.first()
        except Exception:
            return None
        return None if row is None else str(row[0])

    async def dispose(self) -> None:
        await self._engine.dispose()


def _normalize_lock_name(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "_-." else "_" for ch in name)
    return cleaned[:MAX_LOCK_NAME_LENGTH]


@asynccontextmanager
async def named_lock(
    session: AsyncSession, name: str, *, timeout_seconds: int = 0
) -> AsyncIterator[None]:
    """Acquire a MySQL named lock for the duration of the block.

    Named locks give a cluster-wide mutex without Redis, and are used to make
    sure a single mutating portal sequence runs per account at a time.

    The lock lives on a dedicated connection rather than on the caller's
    session: a MySQL named lock belongs to the connection that took it, and a
    commit inside the block would hand the session a different connection,
    leaving the lock held for ever by a pooled one.
    """
    lock_name = _normalize_lock_name(name)
    bind = session.bind
    if not isinstance(bind, AsyncEngine):  # pragma: no cover - misconfiguration
        raise LockUnavailableError("the session is not bound to an async engine")

    async with bind.connect() as connection:
        acquired = await connection.scalar(
            text("SELECT GET_LOCK(:name, :timeout)"),
            {"name": lock_name, "timeout": timeout_seconds},
        )
        if acquired != 1:
            raise LockUnavailableError("could not acquire the named lock", lock=lock_name)
        try:
            yield
        finally:
            await connection.exec_driver_sql(f"SELECT RELEASE_LOCK('{lock_name}')")
