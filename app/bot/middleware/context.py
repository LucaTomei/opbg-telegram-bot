"""Middleware injecting the container, a database session and a correlation id."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.composition import Container
from app.db.session import bind_session
from app.logging import correlation_scope, get_logger

logger = get_logger(__name__)


class ContextMiddleware(BaseMiddleware):
    """Open one transactional session per update and bind a correlation id."""

    def __init__(self, container: Container) -> None:
        self._container = container

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        with correlation_scope() as correlation_id:
            async with self._container.database.session() as session, bind_session(session):
                data["container"] = self._container
                data["settings"] = self._container.settings
                data["session"] = session
                data["correlation_id"] = correlation_id
                return await handler(event, data)
