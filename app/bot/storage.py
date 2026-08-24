"""MySQL backed aiogram FSM storage.

Conversation state must survive a restart, so it is persisted encrypted in the
``conversation_states`` table instead of living in process memory.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey

from app.config import Settings
from app.db.repositories.conversations import ConversationStateRepository
from app.db.repositories.users import UserRepository
from app.db.session import Database

PAYLOAD_KEY = "data"


class MySQLStorage(BaseStorage):
    """Persist aiogram FSM state and data in MySQL, encrypted at rest."""

    def __init__(self, database: Database, settings: Settings) -> None:
        self._database = database
        self._settings = settings

    async def _internal_user_id(self, key: StorageKey) -> uuid.UUID | None:
        async with self._database.read_session() as session:
            user = await UserRepository(session).get_by_telegram_id(key.user_id)
            return None if user is None else user.id

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        user_id = await self._internal_user_id(key)
        if user_id is None:
            return
        resolved = state.state if isinstance(state, State) else state
        async with self._database.session() as session:
            repository = ConversationStateRepository(session)
            if resolved is None:
                await repository.clear(user_id)
                return
            current = await repository.get_for_user(user_id)
            payload = current.payload if current is not None else None
            await repository.set_state(
                user_id,
                state=str(resolved)[:64],
                payload=payload,
                ttl_seconds=self._settings.conversation_state_ttl_seconds,
            )

    async def get_state(self, key: StorageKey) -> str | None:
        user_id = await self._internal_user_id(key)
        if user_id is None:
            return None
        async with self._database.session() as session:
            record = await ConversationStateRepository(session).get_for_user(user_id)
            return None if record is None else record.state

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        user_id = await self._internal_user_id(key)
        if user_id is None:
            return
        async with self._database.session() as session:
            repository = ConversationStateRepository(session)
            current = await repository.get_for_user(user_id)
            await repository.set_state(
                user_id,
                state=current.state if current is not None else "",
                payload={PAYLOAD_KEY: dict(data)},
                ttl_seconds=self._settings.conversation_state_ttl_seconds,
            )

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        user_id = await self._internal_user_id(key)
        if user_id is None:
            return {}
        async with self._database.session() as session:
            record = await ConversationStateRepository(session).get_for_user(user_id)
            if record is None:
                return {}
            payload = record.payload
            if isinstance(payload, dict):
                stored = payload.get(PAYLOAD_KEY)
                if isinstance(stored, dict):
                    return dict(stored)
            return {}

    async def close(self) -> None:
        return None
