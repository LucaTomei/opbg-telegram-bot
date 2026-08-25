"""Operational command line used by the container entrypoint and by scripts."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.composition import build_container
from app.config import get_settings
from app.db.session import named_lock
from app.logging import configure_logging, get_logger
from app.version import APP_VERSION, SCHEMA_VERSION

logger = get_logger(__name__)

MIGRATION_LOCK = "opbg_migrations"
MIGRATION_LOCK_TIMEOUT_SECONDS = 300


def _alembic_config() -> Config:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    return config


async def _migrate() -> int:
    """Upgrade the schema to head under a MySQL named lock.

    The lock makes a rolling deploy safe: only one container applies the
    migrations while the others wait.
    """
    settings = get_settings()
    container = build_container(service_name="migrate", settings=settings, configure_logs=False)
    try:
        async with (
            container.database.session() as session,
            named_lock(session, MIGRATION_LOCK, timeout_seconds=MIGRATION_LOCK_TIMEOUT_SECONDS),
        ):
            await asyncio.to_thread(command.upgrade, _alembic_config(), "head")
            revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
        logger.info(
            "migrations applied",
            extra={"event_code": "MIGRATIONS_APPLIED", "revision": str(revision)},
        )
    finally:
        await container.dispose()
    return 0


async def _downgrade(target: str) -> int:
    settings = get_settings()
    container = build_container(service_name="migrate", settings=settings, configure_logs=False)
    try:
        async with (
            container.database.session() as session,
            named_lock(session, MIGRATION_LOCK, timeout_seconds=MIGRATION_LOCK_TIMEOUT_SECONDS),
        ):
            await asyncio.to_thread(command.downgrade, _alembic_config(), target)
    finally:
        await container.dispose()
    return 0


async def _healthcheck() -> int:
    """Container health probe: configuration valid and MySQL reachable."""
    settings = get_settings()
    container = build_container(service_name="healthcheck", settings=settings, configure_logs=False)
    try:
        healthy = await container.database.ping()
        revision = await container.database.current_schema_revision()
    finally:
        await container.dispose()
    if not healthy or revision is None:
        return 1
    return 0


async def _bootstrap_admins() -> int:
    settings = get_settings()
    container = build_container(service_name="bootstrap", settings=settings, configure_logs=False)
    try:
        async with container.database.session() as session:
            admins = await container.users.bootstrap_admins(session)
    finally:
        await container.dispose()
    logger.info(
        "administrators synchronised",
        extra={"event_code": "ADMIN_BOOTSTRAP_CLI", "count": len(admins)},
    )
    return 0


async def _cleanup() -> int:
    from app.worker.tasks import CleanupTask

    settings = get_settings()
    container = build_container(service_name="cleanup", settings=settings, configure_logs=False)
    try:
        async with container.database.session() as session:
            removed = await CleanupTask(settings).run(session)
    finally:
        await container.dispose()
    logger.info("retention cleanup completed", extra={"event_code": "CLEANUP_CLI", **removed})
    return 0


async def _record_backup(args: argparse.Namespace) -> int:
    """Store the outcome of an external backup job so admins can see it."""
    from datetime import UTC, datetime

    from app.db.repositories.system import BackupStatusRepository

    settings = get_settings()
    container = build_container(service_name="backup", settings=settings, configure_logs=False)
    now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    try:
        async with container.database.session() as session:
            await BackupStatusRepository(session).record(
                started_at=now,
                finished_at=now,
                outcome=args.outcome,
                artifact_name=args.artifact,
                size_bytes=args.size,
                schema_version=SCHEMA_VERSION,
                error_detail=args.error,
            )
    finally:
        await container.dispose()
    return 0


async def _portal_check() -> int:
    """Report whether this host can reach the portal, and how fast.

    A booking that fails from a server but works from a phone is almost never
    the flow: it is DNS, egress or the portal answering that host differently.
    This says which, without credentials and without booking anything.
    """
    import socket
    import time

    import httpx

    settings = get_settings()
    host = httpx.URL(settings.portal_base_url).host
    outcome = 0

    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        addresses = sorted({info[4][0] for info in infos})
        logger.info(
            "portal name resolved",
            extra={"event_code": "PORTAL_CHECK_DNS", "host": host, "addresses": addresses},
        )
    except OSError as exc:
        logger.error(
            "the portal name does not resolve from this host",
            extra={"event_code": "PORTAL_CHECK_DNS_FAILED", "host": host, "detail": str(exc)},
        )
        return 1

    started = time.monotonic()
    try:
        async with httpx.AsyncClient(
            base_url=settings.portal_base_url,
            timeout=settings.portal_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": settings.portal_user_agent},
        ) as client:
            response = await client.get("/login")
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        logger.error(
            "the portal is not reachable from this host",
            extra={
                "event_code": "PORTAL_CHECK_UNREACHABLE",
                "host": host,
                "seconds": round(time.monotonic() - started, 1),
                "error_type": type(exc).__name__,
            },
        )
        return 1

    elapsed = round(time.monotonic() - started, 1)
    if response.status_code >= 400:
        outcome = 1
    logger.info(
        "portal answered",
        extra={
            "event_code": "PORTAL_CHECK_DONE",
            "host": host,
            "status_code": response.status_code,
            "seconds": elapsed,
            "bytes": len(response.content),
        },
    )
    return outcome


def build_parser() -> argparse.ArgumentParser:
    """Build the command line parser; kept separate so tests can reuse it."""
    parser = argparse.ArgumentParser(prog="opbg", description="Operational commands")
    parser.add_argument("--version", action="version", version=APP_VERSION)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("migrate", help="apply database migrations")
    downgrade = subparsers.add_parser("downgrade", help="revert to a specific revision")
    downgrade.add_argument("target", help="target revision, for example -1")
    subparsers.add_parser("healthcheck", help="verify configuration and database")
    subparsers.add_parser("portal-check", help="verify this host can reach the portal")
    subparsers.add_parser("bootstrap-admins", help="synchronise administrators from the env")
    subparsers.add_parser("cleanup", help="apply the retention policy once")
    backup = subparsers.add_parser("record-backup", help="record a backup outcome")
    backup.add_argument("--outcome", required=True, choices=("SUCCESS", "FAILURE"))
    backup.add_argument("--artifact", default=None)
    backup.add_argument("--size", type=int, default=None)
    backup.add_argument("--error", default=None)
    return parser


async def run_command(argv: list[str] | None = None) -> int:
    """Parse the arguments and execute the requested command."""
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(service="cli", level=get_settings().app_log_level)

    handlers = {
        "migrate": _migrate,
        "healthcheck": _healthcheck,
        "bootstrap-admins": _bootstrap_admins,
        "cleanup": _cleanup,
        "portal-check": _portal_check,
    }
    handler = handlers.get(args.command)
    if handler is not None:
        return await handler()
    if args.command == "downgrade":
        return await _downgrade(args.target)
    if args.command == "record-backup":
        return await _record_backup(args)
    parser.error(f"unknown command: {args.command}")
    return 2


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run_command(argv))


if __name__ == "__main__":
    sys.exit(main())
