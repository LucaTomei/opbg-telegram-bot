"""Liveness, readiness and version endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response, status

from app.config import Settings
from app.db.session import Database
from app.version import APP_NAME, APP_VERSION, SCHEMA_VERSION

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def liveness() -> dict[str, str]:
    """Liveness never depends on the portal or on the database."""
    return {"status": "alive", "service": APP_NAME}


@router.get("/health/ready")
async def readiness(request: Request, response: Response) -> dict[str, Any]:
    """Readiness checks configuration, MySQL and the applied schema revision."""
    database: Database = request.app.state.database
    settings: Settings = request.app.state.settings
    database_ok = await database.ping()
    revision = await database.current_schema_revision() if database_ok else None
    schema_ok = revision is not None
    ready = database_ok and schema_ok
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ready" if ready else "not-ready",
        "database": database_ok,
        "schema_revision": revision,
        "environment": settings.app_env,
    }


@router.get("/version")
async def version() -> dict[str, str]:
    return {"name": APP_NAME, "version": APP_VERSION, "schema": SCHEMA_VERSION}
