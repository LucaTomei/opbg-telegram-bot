"""Migrations must build the whole schema from an empty database."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.db.base import Base
from tests.conftest import integration_database_url

MIGRATION_DATABASE = "opbg_bot_migrations"


def sync_url(database: str | None = None) -> str:
    url = integration_database_url().replace("+aiomysql", "+pymysql")
    base, _, query = url.partition("?")
    prefix, _, _ = base.rpartition("/")
    target = f"{prefix}/{database}" if database else prefix
    return f"{target}?{query}" if query else target


@pytest.fixture(scope="module")
def migration_database() -> Iterator[str]:
    """Create and drop a disposable database used only for migration tests."""
    try:
        server = create_engine(sync_url(), future=True, isolation_level="AUTOCOMMIT")
        with server.connect() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{MIGRATION_DATABASE}`"))
            connection.execute(
                text(
                    f"CREATE DATABASE `{MIGRATION_DATABASE}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
                )
            )
        server.dispose()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"integration database is not available: {type(exc).__name__}")
    yield sync_url(MIGRATION_DATABASE)
    server = create_engine(sync_url(), future=True, isolation_level="AUTOCOMMIT")
    with server.connect() as connection:
        connection.execute(text(f"DROP DATABASE IF EXISTS `{MIGRATION_DATABASE}`"))
    server.dispose()


def alembic_config(url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("script_location", "alembic")
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_upgrade_from_empty_database_creates_every_table(migration_database: str) -> None:
    command.upgrade(alembic_config(migration_database), "head")
    engine = create_engine(migration_database, future=True)
    inspector = sa.inspect(engine)
    tables = set(inspector.get_table_names())
    engine.dispose()
    expected = set(Base.metadata.tables) | {"alembic_version"}
    assert expected <= tables


def test_schema_matches_the_declarative_metadata(migration_database: str) -> None:
    """Autogeneration must produce no operation against a migrated database."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.db.migrations import compare_type

    command.upgrade(alembic_config(migration_database), "head")
    engine = create_engine(migration_database, future=True)
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection=connection,
            opts={"compare_type": compare_type, "target_metadata": Base.metadata},
        )
        differences = compare_metadata(context, Base.metadata)
    engine.dispose()
    assert differences == []


def test_tables_use_innodb_and_utf8mb4(migration_database: str) -> None:
    command.upgrade(alembic_config(migration_database), "head")
    engine = create_engine(migration_database, future=True)
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT table_name, engine, table_collation "
                "FROM information_schema.tables WHERE table_schema = :schema"
            ),
            {"schema": MIGRATION_DATABASE},
        ).all()
    engine.dispose()
    for name, engine_name, collation in rows:
        if name == "alembic_version":
            continue
        assert engine_name == "InnoDB", name
        assert str(collation).startswith("utf8mb4"), name


def test_downgrade_removes_every_table(migration_database: str) -> None:
    config = alembic_config(migration_database)
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    engine = create_engine(migration_database, future=True)
    inspector = sa.inspect(engine)
    remaining = set(inspector.get_table_names()) - {"alembic_version"}
    engine.dispose()
    assert remaining == set()
    command.upgrade(config, "head")


def test_migration_is_idempotent(migration_database: str) -> None:
    config = alembic_config(migration_database)
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    engine = create_engine(migration_database, future=True)
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    engine.dispose()
    assert revision == "0001_initial"


def test_environment_variable_can_target_another_server() -> None:
    """The suite must never hardcode a production database."""
    assert "TEST_DATABASE_URL" in os.environ or "127.0.0.1" in integration_database_url()
