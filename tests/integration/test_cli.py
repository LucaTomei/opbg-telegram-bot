"""Operational command line used by the container entrypoint and by scripts."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import cli
from app.composition import Container
from app.config import Settings
from app.db.models.users import TelegramUser
from app.db.repositories.accounts import ConnectTicketRepository
from app.db.repositories.base import utc_now
from app.db.repositories.system import BackupStatusRepository
from app.db.repositories.users import UserRepository
from app.db.session import Database
from app.domain.enums import ConnectTicketPurpose, UserRole
from app.security.tokens import generate_token


@pytest.fixture(autouse=True)
def patched_cli(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, container: Container
) -> Iterator[None]:
    """Run the CLI against the test container instead of the real environment."""

    async def keep_engine_alive(_self: Database) -> None:
        """The session-scoped engine must survive every CLI invocation."""
        return

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "build_container", lambda **_kwargs: container)
    monkeypatch.setattr(Database, "dispose", keep_engine_alive)
    yield


async def test_the_parser_rejects_an_unknown_command() -> None:
    with pytest.raises(SystemExit):
        await cli.run_command(["nonesiste"])


async def test_the_version_is_exposed() -> None:
    with pytest.raises(SystemExit) as exit_info:
        await cli.run_command(["--version"])
    assert exit_info.value.code == 0


async def test_healthcheck_succeeds_against_a_migrated_database(
    clean_session: AsyncSession,
) -> None:
    assert await cli.run_command(["healthcheck"]) == 0


async def test_bootstrap_admins_promotes_the_configured_ids(
    clean_session: AsyncSession, database: Database
) -> None:
    assert await cli.run_command(["bootstrap-admins"]) == 0


async def test_bootstrap_admins_is_visible_in_the_database(
    clean_session: AsyncSession, database: Database, settings: Settings
) -> None:
    await cli.run_command(["bootstrap-admins"])
    async with database.read_session() as session:
        admin = await UserRepository(session).get_by_telegram_id(settings.admin_telegram_ids[0])
        assert admin is not None
        assert admin.role is UserRole.ADMIN


async def test_cleanup_removes_expired_records(
    clean_session: AsyncSession, database: Database, user: TelegramUser
) -> None:
    tickets = ConnectTicketRepository(clean_session)
    ticket = await tickets.create(
        user_id=user.id,
        token=generate_token(),
        purpose=ConnectTicketPurpose.CONNECT,
        ttl_minutes=1,
    )
    ticket.expires_at = utc_now() - timedelta(days=2)
    await clean_session.commit()

    assert await cli.run_command(["cleanup"]) == 0

    async with database.read_session() as session:
        assert await ConnectTicketRepository(session).get(ticket.id) is None


async def test_record_backup_stores_the_outcome(
    clean_session: AsyncSession, database: Database
) -> None:
    await clean_session.commit()
    assert (
        await cli.run_command(
            [
                "record-backup",
                "--outcome",
                "SUCCESS",
                "--artifact",
                "opbg-test.sql.gz.enc",
                "--size",
                "1234",
            ]
        )
        == 0
    )
    async with database.read_session() as session:
        latest = await BackupStatusRepository(session).latest()
        assert latest is not None
        assert latest.outcome == "SUCCESS"
        assert latest.artifact_name == "opbg-test.sql.gz.enc"
        assert latest.size_bytes == 1234


async def test_a_failed_backup_is_recorded_with_a_redacted_detail(
    clean_session: AsyncSession, database: Database
) -> None:
    await clean_session.commit()
    await cli.run_command(
        [
            "record-backup",
            "--outcome",
            "FAILURE",
            "--error",
            "mysqldump failed for utente@example.invalid",
        ]
    )
    async with database.read_session() as session:
        latest = await BackupStatusRepository(session).latest()
        assert latest is not None
        assert latest.outcome == "FAILURE"
        assert latest.error_detail is not None
        assert "utente@example.invalid" not in latest.error_detail


def test_the_alembic_configuration_points_at_the_repository() -> None:
    config = cli._alembic_config()
    location = config.get_main_option("script_location")
    assert location is not None
    assert location.endswith("alembic")
