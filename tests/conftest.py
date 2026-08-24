"""Shared pytest fixtures.

Unit, contract and security tests run without any external dependency.
Integration tests require a disposable MySQL instance; its URL comes from
``TEST_DATABASE_URL`` and defaults to the local development container.
"""

from __future__ import annotations

import base64
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.config import Settings
from app.db.base import Base
from app.db.models import TelegramUser
from app.db.session import Database, create_session_factory
from app.domain.enums import UserRole, UserStatus
from app.security.blind_index import BlindIndexService
from app.security.context import configure_security
from app.security.encryption import EncryptionService, generate_key_material

if TYPE_CHECKING:
    from app.composition import Container
    from tests.support.portal import FakePortal

#: Deterministic key material so encrypted fixtures stay reproducible.
TEST_ENCRYPTION_KEY = bytes(range(32))
TEST_BLIND_INDEX_KEY = bytes(range(32, 64))

DEFAULT_TEST_DATABASE_URL = (
    "mysql+aiomysql://root:devrootpwd@127.0.0.1:13306/opbg_bot_test?charset=utf8mb4"
)


def _test_settings_env() -> dict[str, str]:
    key = base64.urlsafe_b64encode(TEST_ENCRYPTION_KEY[:32]).decode()
    blind = base64.urlsafe_b64encode(TEST_BLIND_INDEX_KEY[:32]).decode()
    return {
        "APP_ENV": "test",
        "APP_LOG_LEVEL": "WARNING",
        "APP_TIMEZONE": "Europe/Rome",
        "APP_SECRET_KEY": base64.urlsafe_b64encode(bytes(range(64, 96))).decode(),
        "APP_ENCRYPTION_KEY": key,
        "APP_BLIND_INDEX_KEY": blind,
        "PUBLIC_BASE_URL": "https://bot.test.invalid",
        "TELEGRAM_BOT_TOKEN": "424242:AAH0000000000000000000000000000000",
        "TELEGRAM_BOT_USERNAME": "opbg_test_bot",
        "TELEGRAM_ADMIN_IDS": "1001",
        "MYSQL_PASSWORD": base64.urlsafe_b64encode(bytes(range(96, 128))).decode(),
        "DATABASE_HOST": "127.0.0.1",
        "DATABASE_PORT": "13306",
        "PORTAL_BASE_URL": "https://portal.test.invalid",
    }


@pytest.fixture(scope="session")
def settings() -> Settings:
    """Validated settings built from a deterministic test environment."""
    return Settings(
        _env_file=None,
        **{key.lower(): value for key, value in _test_settings_env().items()},
    )


@pytest.fixture(autouse=True)
def security(settings: Settings) -> Iterator[None]:
    """Install deterministic encryption and blind-index services for every test."""
    configure_security(
        EncryptionService(active_key=settings.active_encryption_key, active_key_version=1),
        BlindIndexService(master_key=settings.blind_index_key),
    )
    yield


@pytest.fixture
def encryption() -> EncryptionService:
    return EncryptionService(active_key=generate_key_material())


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 3, 2, 8, 30, tzinfo=UTC)


def integration_database_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)


@pytest.fixture(scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    """Create the schema once per session on a disposable MySQL database."""
    url = integration_database_url()
    engine = create_async_engine(url, pool_pre_ping=True, future=True)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
    except Exception as exc:  # pragma: no cover - environment dependent
        await engine.dispose()
        pytest.skip(f"integration database is not available: {type(exc).__name__}")
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session bound to a transaction that is rolled back after each test."""
    factory = create_session_factory(engine)
    async with factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def clean_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session that truncates every table before the test runs."""
    factory = create_session_factory(engine)
    async with engine.begin() as connection:
        await connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS = 0")
        for table in reversed(Base.metadata.sorted_tables):
            await connection.exec_driver_sql(f"TRUNCATE TABLE `{table.name}`")
        await connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS = 1")
    async with factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def database(engine: AsyncEngine, settings: Settings) -> Database:
    return Database(settings, engine=engine)


@pytest.fixture
async def user(clean_session: AsyncSession) -> TelegramUser:
    record = TelegramUser(
        telegram_id=500001,
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        consent_version="1.0",
        consented_at=datetime.now(UTC).replace(tzinfo=None, microsecond=0),
    )
    clean_session.add(record)
    await clean_session.flush()
    return record


@pytest.fixture
async def admin_user(clean_session: AsyncSession) -> TelegramUser:
    record = TelegramUser(
        telegram_id=1001,
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
        consent_version="1.0",
        consented_at=datetime.now(UTC).replace(tzinfo=None, microsecond=0),
    )
    clean_session.add(record)
    await clean_session.flush()
    return record


@pytest.fixture
def uuid_factory() -> type[uuid.UUID]:
    return uuid.UUID


@pytest.fixture
def portal() -> "FakePortal":
    """A deterministic synthetic portal shared by the service level tests."""
    from tests.support.portal import FakePortal

    return FakePortal()


@pytest.fixture
def container(settings: Settings, database: Database, portal: "FakePortal") -> "Container":
    """A fully wired container whose portal adapter talks to the fake portal."""
    from tests.support.portal import build_container

    return build_container(settings, database, portal)
