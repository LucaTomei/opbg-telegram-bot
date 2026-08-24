"""Alembic environment.

Migrations run against the synchronous MySQL driver so that ``alembic`` can be
invoked from a plain command line, while the application itself uses the async
driver.  The database URL always comes from validated application settings, so
a migration can never run against an unconfigured environment.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.sql.sqltypes import _Binary
from sqlalchemy.types import TypeEngine

from app.config import get_settings
from app.db.models import Base
from app.db.types import Ciphertext

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _compare_type(
    context_: object,
    inspected_column: object,
    metadata_column: object,
    inspected_type: TypeEngine[object],
    metadata_type: TypeEngine[object],
) -> bool | None:
    """Treat every BLOB variant as equivalent to :class:`Ciphertext`.

    MySQL maps a ``LargeBinary`` length to one of TINYBLOB/BLOB/MEDIUMBLOB, so
    reflection never returns the declared type decorator.  Returning ``False``
    here avoids an endless "type changed" diff on encrypted columns.
    """
    if isinstance(metadata_type, Ciphertext) and isinstance(inspected_type, _Binary):
        return False
    return None


def _database_url() -> str:
    override = config.get_main_option("sqlalchemy.url", None)
    if override:
        return override
    return get_settings().sync_database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=_compare_type,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=_compare_type,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
