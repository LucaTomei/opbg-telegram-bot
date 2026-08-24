"""Append-only administrative audit repository.

The repository exposes no update or delete operation.  Attempting to modify a
persisted entry raises immediately, and an integration test asserts that the
behaviour holds at the ORM level.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Select, event, select
from sqlalchemy.orm import Mapper
from sqlalchemy.orm.session import ORMExecuteState

from app.db.models.system import AdminAuditLog
from app.db.repositories.base import BaseRepository, Page, utc_now
from app.domain.enums import AuditAction
from app.domain.errors import PermissionDeniedError


class AuditLogImmutableError(PermissionDeniedError):
    code = "AUDIT_LOG_IMMUTABLE"


@event.listens_for(AdminAuditLog, "before_update", propagate=True)
def _forbid_update(_mapper: Mapper[AdminAuditLog], _connection: object, _target: object) -> None:
    raise AuditLogImmutableError("the administrative audit log is append-only")


@event.listens_for(AdminAuditLog, "before_delete", propagate=True)
def _forbid_delete(_mapper: Mapper[AdminAuditLog], _connection: object, _target: object) -> None:
    raise AuditLogImmutableError("the administrative audit log is append-only")


class AuditRepository(BaseRepository[AdminAuditLog]):
    model = AdminAuditLog

    async def record(
        self,
        *,
        admin_user_id: uuid.UUID,
        action: AuditAction,
        target_type: str,
        target_id: str,
        reason: str | None = None,
        before_hash: str | None = None,
        after_hash: str | None = None,
        correlation_id: str | None = None,
    ) -> AdminAuditLog:
        entry = AdminAuditLog(
            admin_user_id=admin_user_id,
            action=action,
            target_type=target_type[:32],
            target_id=target_id[:64],
            reason=None if reason is None else reason[:512],
            before_hash=before_hash,
            after_hash=after_hash,
            correlation_id=None if correlation_id is None else correlation_id[:32],
            created_at=utc_now(),
        )
        return await self.add(entry)

    async def list_entries(
        self,
        *,
        page: int,
        page_size: int,
        admin_user_id: uuid.UUID | None = None,
        action: AuditAction | None = None,
        target_id: str | None = None,
        correlation_id: str | None = None,
        since: datetime | None = None,
    ) -> Page[AdminAuditLog]:
        statement: Select[tuple[AdminAuditLog]] = select(AdminAuditLog).order_by(
            AdminAuditLog.created_at.desc()
        )
        if admin_user_id is not None:
            statement = statement.where(AdminAuditLog.admin_user_id == admin_user_id)
        if action is not None:
            statement = statement.where(AdminAuditLog.action == action)
        if target_id is not None:
            statement = statement.where(AdminAuditLog.target_id == target_id[:64])
        if correlation_id is not None:
            statement = statement.where(AdminAuditLog.correlation_id == correlation_id[:32])
        if since is not None:
            statement = statement.where(AdminAuditLog.created_at >= since)
        return await self.paginate(statement, page=page, page_size=page_size)


def guard_audit_statement(state: ORMExecuteState) -> None:
    """Reject bulk updates or deletes targeting the audit table."""
    if not (state.is_update or state.is_delete):
        return
    for entity in state.all_mappers:
        if entity.class_ is AdminAuditLog:
            raise AuditLogImmutableError("the administrative audit log is append-only")
