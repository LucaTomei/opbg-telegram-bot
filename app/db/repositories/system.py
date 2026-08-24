"""Repositories for system events, worker heartbeats and backup status."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, delete, select

from app.db.models.system import (
    BackupStatus,
    PortalCallLog,
    SystemEvent,
    WorkerHeartbeat,
)
from app.db.repositories.base import BaseRepository, Page, utc_now
from app.domain.enums import Severity
from app.security.redaction import redact_text
from app.version import APP_VERSION


class SystemEventRepository(BaseRepository[SystemEvent]):
    model = SystemEvent

    async def record(
        self,
        *,
        severity: Severity,
        component: str,
        event_code: str,
        message: str,
        context: dict[str, Any] | None = None,
        correlation_id: str | None = None,
        portal_account_id: uuid.UUID | None = None,
        telegram_user_id: uuid.UUID | None = None,
    ) -> SystemEvent:
        """Persist an operational event with a redacted message and context."""
        from app.security.redaction import redact_value

        event = SystemEvent(
            severity=severity,
            component=component[:48],
            event_code=event_code[:64],
            message=redact_text(message)[:1024],
            context_json=None if context is None else redact_value(context),
            correlation_id=None if correlation_id is None else correlation_id[:32],
            portal_account_id=portal_account_id,
            telegram_user_id=telegram_user_id,
        )
        return await self.add(event)

    async def list_events(
        self,
        *,
        page: int,
        page_size: int,
        severity: Severity | None = None,
        component: str | None = None,
        correlation_id: str | None = None,
        only_unresolved: bool = False,
        since: datetime | None = None,
    ) -> Page[SystemEvent]:
        statement: Select[tuple[SystemEvent]] = select(SystemEvent).order_by(
            SystemEvent.created_at.desc()
        )
        if severity is not None:
            statement = statement.where(SystemEvent.severity == severity)
        if component is not None:
            statement = statement.where(SystemEvent.component == component[:48])
        if correlation_id is not None:
            statement = statement.where(SystemEvent.correlation_id == correlation_id[:32])
        if only_unresolved:
            statement = statement.where(SystemEvent.resolved_at.is_(None))
        if since is not None:
            statement = statement.where(SystemEvent.created_at >= since)
        return await self.paginate(statement, page=page, page_size=page_size)

    async def resolve(self, event: SystemEvent, *, admin_user_id: uuid.UUID, note: str) -> None:
        event.resolved_at = utc_now()
        event.resolved_by_user_id = admin_user_id
        event.resolution_note = redact_text(note)[:512]
        await self.flush()

    async def count_unresolved(self, *, severity: Severity | None = None) -> int:
        statement = select(SystemEvent).where(SystemEvent.resolved_at.is_(None))
        if severity is not None:
            statement = statement.where(SystemEvent.severity == severity)
        return await self.count(statement)

    async def purge_old(self, *, before: datetime) -> int:
        return await self.execute_dml(
            delete(SystemEvent).where(
                SystemEvent.created_at < before, SystemEvent.resolved_at.isnot(None)
            )
        )


class HeartbeatRepository(BaseRepository[WorkerHeartbeat]):
    model = WorkerHeartbeat

    async def beat(
        self, *, worker_id: str, started_at: datetime, metadata: dict[str, Any] | None = None
    ) -> WorkerHeartbeat:
        record = await self.one_or_none(
            select(WorkerHeartbeat).where(WorkerHeartbeat.worker_id == worker_id[:64])
        )
        now = utc_now()
        if record is None:
            record = WorkerHeartbeat(
                worker_id=worker_id[:64],
                started_at=started_at,
                last_seen_at=now,
                app_version=APP_VERSION,
                metadata_json=metadata,
            )
            return await self.add(record)
        record.last_seen_at = now
        record.app_version = APP_VERSION
        record.metadata_json = metadata
        await self.flush()
        return record

    async def list_workers(self) -> list[WorkerHeartbeat]:
        result = await self.session.scalars(
            select(WorkerHeartbeat).order_by(WorkerHeartbeat.worker_id)
        )
        return list(result)

    async def has_recent(self, *, max_age_seconds: int) -> bool:
        threshold = utc_now() - timedelta(seconds=max_age_seconds)
        found = await self.one_or_none(
            select(WorkerHeartbeat).where(WorkerHeartbeat.last_seen_at >= threshold).limit(1)
        )
        return found is not None

    async def purge_stale(self, *, before: datetime) -> int:
        return await self.execute_dml(
            delete(WorkerHeartbeat).where(WorkerHeartbeat.last_seen_at < before)
        )


class PortalCallLogRepository(BaseRepository[PortalCallLog]):
    model = PortalCallLog

    async def record(
        self,
        *,
        operation: str,
        outcome: str,
        portal_account_id: uuid.UUID | None = None,
        status_code: int | None = None,
        duration_ms: int | None = None,
    ) -> PortalCallLog:
        record = PortalCallLog(
            created_at=utc_now(),
            portal_account_id=portal_account_id,
            operation=operation[:48],
            outcome=outcome[:24],
            status_code=status_code,
            duration_ms=duration_ms,
        )
        return await self.add(record)

    async def recent_count(self, *, window_seconds: int) -> int:
        threshold = utc_now() - timedelta(seconds=window_seconds)
        return await self.count(select(PortalCallLog).where(PortalCallLog.created_at >= threshold))

    async def purge_old(self, *, before: datetime) -> int:
        return await self.execute_dml(
            delete(PortalCallLog).where(PortalCallLog.created_at < before)
        )


class BackupStatusRepository(BaseRepository[BackupStatus]):
    model = BackupStatus

    async def record(
        self,
        *,
        started_at: datetime,
        finished_at: datetime | None,
        outcome: str,
        artifact_name: str | None = None,
        size_bytes: int | None = None,
        schema_version: str | None = None,
        error_detail: str | None = None,
    ) -> BackupStatus:
        record = BackupStatus(
            started_at=started_at,
            finished_at=finished_at,
            outcome=outcome[:24],
            artifact_name=None if artifact_name is None else artifact_name[:255],
            size_bytes=size_bytes,
            schema_version=None if schema_version is None else schema_version[:64],
            error_detail=None if error_detail is None else redact_text(error_detail)[:512],
        )
        return await self.add(record)

    async def latest(self) -> BackupStatus | None:
        return await self.one_or_none(
            select(BackupStatus).order_by(BackupStatus.started_at.desc()).limit(1)
        )
