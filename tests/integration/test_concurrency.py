"""Leases, locks, optimistic locking and audit immutability."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.composition import Container
from app.db.models.system import AdminAuditLog
from app.db.models.users import TelegramUser
from app.db.repositories.audit import AuditLogImmutableError, AuditRepository
from app.db.repositories.base import utc_now
from app.db.repositories.watches import WatchRepository
from app.db.session import create_session_factory, named_lock
from app.domain.enums import AuditAction
from app.domain.errors import LockUnavailableError, OptimisticLockError
from app.services.locks import account_lock, account_lock_name
from tests.support.factories import make_account, make_watch


async def test_only_one_worker_leases_a_due_monitor(
    clean_session: AsyncSession, engine: AsyncEngine, user: TelegramUser
) -> None:
    account = await make_account(clean_session, user)
    watch = await make_watch(
        clean_session, user, account, next_check_at=utc_now() - timedelta(minutes=1)
    )
    await clean_session.commit()

    factory = create_session_factory(engine)

    async def claim(worker_id: str) -> list[str]:
        async with factory() as session:
            leased = await WatchRepository(session).claim_due(
                worker_id=worker_id, now=utc_now(), lease_seconds=60, limit=10
            )
            identifiers = [item.id.hex for item in leased]
            await session.commit()
            return identifiers

    first, second = await asyncio.gather(claim("worker-a"), claim("worker-b"))
    assert sorted([*first, *second]) == [watch.id.hex]


async def test_an_expired_lease_is_recovered(
    clean_session: AsyncSession, engine: AsyncEngine, user: TelegramUser
) -> None:
    account = await make_account(clean_session, user)
    watch = await make_watch(
        clean_session, user, account, next_check_at=utc_now() - timedelta(minutes=5)
    )
    watch.lease_owner = "dead-worker"
    watch.lease_expires_at = utc_now() - timedelta(minutes=1)
    await clean_session.commit()

    factory = create_session_factory(engine)
    async with factory() as session:
        leased = await WatchRepository(session).claim_due(
            worker_id="worker-b", now=utc_now(), lease_seconds=60, limit=10
        )
        await session.commit()
    assert [item.id for item in leased] == [watch.id]
    assert leased[0].lease_owner == "worker-b"


async def test_a_live_lease_is_not_stolen(
    clean_session: AsyncSession, engine: AsyncEngine, user: TelegramUser
) -> None:
    account = await make_account(clean_session, user)
    watch = await make_watch(
        clean_session, user, account, next_check_at=utc_now() - timedelta(minutes=5)
    )
    watch.lease_owner = "worker-a"
    watch.lease_expires_at = utc_now() + timedelta(minutes=5)
    await clean_session.commit()

    factory = create_session_factory(engine)
    async with factory() as session:
        leased = await WatchRepository(session).claim_due(
            worker_id="worker-b", now=utc_now(), lease_seconds=60, limit=10
        )
        await session.commit()
    assert leased == []


async def test_lease_renewal_requires_ownership(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    account = await make_account(clean_session, user)
    watch = await make_watch(clean_session, user, account)
    watch.lease_owner = "worker-a"
    watch.lease_expires_at = utc_now() + timedelta(seconds=30)
    watches = WatchRepository(clean_session)
    assert await watches.renew_lease(watch, worker_id="worker-b", lease_seconds=60) is False
    assert await watches.renew_lease(watch, worker_id="worker-a", lease_seconds=60) is True


async def test_shutdown_releases_every_lease_of_a_worker(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    account = await make_account(clean_session, user)
    watches = WatchRepository(clean_session)
    for _ in range(3):
        watch = await make_watch(clean_session, user, account)
        watch.lease_owner = "worker-a"
        watch.lease_expires_at = utc_now() + timedelta(seconds=30)
    await watches.flush()
    assert await watches.release_leases_of_worker("worker-a") == 3


async def test_account_lock_is_exclusive(
    clean_session: AsyncSession, engine: AsyncEngine, user: TelegramUser
) -> None:
    account = await make_account(clean_session, user)
    await clean_session.commit()
    factory = create_session_factory(engine)

    async with factory() as holder, account_lock(holder, account.id), factory() as competitor:
        with pytest.raises(LockUnavailableError):
            async with account_lock(competitor, account.id):
                pass


async def test_the_lock_is_released_after_the_block(
    clean_session: AsyncSession, engine: AsyncEngine, user: TelegramUser
) -> None:
    account = await make_account(clean_session, user)
    await clean_session.commit()
    factory = create_session_factory(engine)
    async with factory() as first, account_lock(first, account.id):
        pass
    async with factory() as second, account_lock(second, account.id):
        pass


async def test_lock_names_are_stable_and_short(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    account = await make_account(clean_session, user)
    name = account_lock_name(account.id)
    assert name == account_lock_name(account.id)
    assert len(name) <= 64


async def test_named_lock_timeout_is_reported(
    clean_session: AsyncSession, engine: AsyncEngine
) -> None:
    factory = create_session_factory(engine)
    async with (
        factory() as holder,
        named_lock(holder, "opbg_test_lock"),
        factory() as competitor,
    ):
        with pytest.raises(LockUnavailableError):
            async with named_lock(competitor, "opbg_test_lock", timeout_seconds=0):
                pass


async def test_optimistic_locking_detects_a_concurrent_update(
    clean_session: AsyncSession, engine: AsyncEngine, user: TelegramUser
) -> None:
    account = await make_account(clean_session, user)
    watch = await make_watch(clean_session, user, account)
    await clean_session.commit()

    factory = create_session_factory(engine)
    async with factory() as first, factory() as second:
        one = await WatchRepository(first).get_any(watch.id)
        two = await WatchRepository(second).get_any(watch.id)
        assert one is not None and two is not None
        one.name = "Rinominato dal primo"
        await first.commit()
        two.name = "Rinominato dal secondo"
        with pytest.raises(OptimisticLockError):
            await WatchRepository(second).flush()
        await second.rollback()


async def test_version_increases_on_every_update(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    account = await make_account(clean_session, user)
    watch = await make_watch(clean_session, user, account)
    initial = watch.version
    watch.name = "Nuovo nome"
    await WatchRepository(clean_session).flush()
    assert watch.version == initial + 1


# --- audit -------------------------------------------------------------------
async def test_audit_entries_cannot_be_updated(
    clean_session: AsyncSession, admin_user: TelegramUser
) -> None:
    audit = AuditRepository(clean_session)
    entry = await audit.record(
        admin_user_id=admin_user.id,
        action=AuditAction.REVEAL_PII,
        target_type="telegram_user",
        target_id=admin_user.id.hex,
        reason="verifica di test",
    )
    entry.reason = "manomesso"
    with pytest.raises(AuditLogImmutableError):
        await clean_session.flush()
    await clean_session.rollback()


async def test_audit_entries_cannot_be_deleted(
    clean_session: AsyncSession, admin_user: TelegramUser
) -> None:
    audit = AuditRepository(clean_session)
    entry = await audit.record(
        admin_user_id=admin_user.id,
        action=AuditAction.INVITE_CREATED,
        target_type="invite",
        target_id="abc",
        reason="creazione invito",
    )
    await clean_session.delete(entry)
    with pytest.raises(AuditLogImmutableError):
        await clean_session.flush()
    await clean_session.rollback()


async def test_audit_entries_are_listed_with_filters(
    clean_session: AsyncSession, admin_user: TelegramUser
) -> None:
    audit = AuditRepository(clean_session)
    await audit.record(
        admin_user_id=admin_user.id,
        action=AuditAction.REVEAL_PII,
        target_type="telegram_user",
        target_id="one",
        reason="motivo uno",
        correlation_id="cid-1",
    )
    await audit.record(
        admin_user_id=admin_user.id,
        action=AuditAction.USER_STATUS_CHANGED,
        target_type="telegram_user",
        target_id="two",
        reason="motivo due",
        correlation_id="cid-2",
    )
    by_action = await audit.list_entries(page=0, page_size=10, action=AuditAction.REVEAL_PII)
    assert by_action.total == 1
    by_correlation = await audit.list_entries(page=0, page_size=10, correlation_id="cid-2")
    assert by_correlation.total == 1


async def test_audit_never_stores_a_secret(
    clean_session: AsyncSession, admin_user: TelegramUser
) -> None:
    audit = AuditRepository(clean_session)
    await audit.record(
        admin_user_id=admin_user.id,
        action=AuditAction.ACCOUNT_STATUS_CHANGED,
        target_type="portal_account",
        target_id="abc",
        reason="disattivazione",
        before_hash="hash-prima",
        after_hash="hash-dopo",
    )
    rows = (await clean_session.scalars(select(AdminAuditLog))).all()
    for row in rows:
        assert "password" not in (row.reason or "")
        assert row.before_hash != "ACTIVE"


async def test_a_commit_inside_the_lock_does_not_leak_it(
    clean_session: AsyncSession, engine: AsyncEngine, user: TelegramUser
) -> None:
    """A named lock must survive — and be released after — a commit.

    MySQL named locks belong to the connection that took them, so a commit in
    the middle of the block used to hand the lock to a pooled connection and
    leave it held for ever.
    """
    account = await make_account(clean_session, user)
    await clean_session.commit()
    factory = create_session_factory(engine)

    async with factory() as holder, account_lock(holder, account.id):
        await holder.commit()

    # A second acquisition proves the lock was really released.
    async with factory() as competitor, account_lock(competitor, account.id):
        pass


async def test_the_lock_is_released_when_the_block_raises(
    clean_session: AsyncSession, engine: AsyncEngine, user: TelegramUser
) -> None:
    account = await make_account(clean_session, user)
    await clean_session.commit()
    factory = create_session_factory(engine)

    with pytest.raises(RuntimeError):
        async with factory() as holder, account_lock(holder, account.id):
            raise RuntimeError("synthetic failure inside the lock")

    async with factory() as competitor, account_lock(competitor, account.id):
        pass


async def test_audit_retention_is_applied_deliberately(
    clean_session: AsyncSession, admin_user: TelegramUser
) -> None:
    """Retention is the one deliberate exception to the append-only rule."""
    audit = AuditRepository(clean_session)
    old = await audit.record(
        admin_user_id=admin_user.id,
        action=AuditAction.REVEAL_PII,
        target_type="telegram_user",
        target_id="old",
        reason="vecchia voce",
    )
    await audit.record(
        admin_user_id=admin_user.id,
        action=AuditAction.REVEAL_PII,
        target_type="telegram_user",
        target_id="recent",
        reason="voce recente",
    )
    # The ORM refuses to update the log, so the age is set with an explicit
    # statement: the point of the test is the retention, not the guard.
    await clean_session.execute(
        text("UPDATE admin_audit_log SET created_at = :when WHERE target_id = 'old'"),
        {"when": utc_now() - timedelta(days=800)},
    )

    clean_session.expunge(old)
    removed = await audit.purge_before(before=utc_now() - timedelta(days=365))
    assert removed == 1
    remaining = await audit.list_entries(page=0, page_size=10)
    assert {item.target_id for item in remaining.items} == {"recent"}


async def test_the_cleanup_task_honours_the_audit_retention(
    clean_session: AsyncSession, container: Container, admin_user: TelegramUser
) -> None:
    from app.worker.tasks import CleanupTask

    await AuditRepository(clean_session).record(
        admin_user_id=admin_user.id,
        action=AuditAction.INVITE_CREATED,
        target_type="invite",
        target_id="fresh",
        reason="voce recente",
    )
    removed = await CleanupTask(container.settings).run(clean_session)
    assert "audit_entries" in removed
    assert removed["audit_entries"] == 0
    remaining = await AuditRepository(clean_session).list_entries(page=0, page_size=10)
    assert remaining.total == 1
