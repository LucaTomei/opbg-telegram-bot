"""MySQL backed aiogram FSM storage.

Conversation state must survive a restart, so it is persisted encrypted in the
``conversation_states`` table instead of living in process memory.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.repositories.conversations import ConversationStateRepository
from app.db.repositories.users import UserRepository
from app.db.session import Database, current_session

PAYLOAD_KEY = "data"


def _has_payload(payload: Any) -> bool:
    """Return ``True`` when the stored payload carries conversation data."""
    return bool(isinstance(payload, dict) and payload.get(PAYLOAD_KEY))


class MySQLStorage(BaseStorage):
    """Persist aiogram FSM state and data in MySQL, encrypted at rest."""

    def __init__(self, database: Database, settings: Settings) -> None:
        self._database = database
        self._settings = settings

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[AsyncSession]:
        """Reuse the session of the current update, or open a dedicated one.

        Sharing the transaction keeps the conversation state atomic with the
        rest of the update and avoids two connections competing for the same
        foreign-key parent rows.
        """
        ambient = current_session()
        if ambient is not None:
            yield ambient
            return
        async with self._database.session() as session:
            yield session

    async def _internal_user_id(self, session: AsyncSession, key: StorageKey) -> uuid.UUID | None:
        user = await UserRepository(session).get_by_telegram_id(key.user_id)
        return None if user is None else user.id

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        resolved = state.state if isinstance(state, State) else state
        async with self._session() as session:
            user_id = await self._internal_user_id(session, key)
            if user_id is None:
                return
            repository = ConversationStateRepository(session)
            current = await repository.get_for_user(user_id)
            if resolved is None:
                # Clearing the state must not discard the data: aiogram keeps
                # the two independent and clears them with separate calls.
                if current is None:
                    return
                if not _has_payload(current.payload):
                    await repository.clear(user_id)
                    return
                await repository.set_state(
                    user_id,
                    state="",
                    payload=current.payload,
                    ttl_seconds=self._settings.conversation_state_ttl_seconds,
                )
                return
            await repository.set_state(
                user_id,
                state=str(resolved)[:64],
                payload=current.payload if current is not None else None,
                ttl_seconds=self._settings.conversation_state_ttl_seconds,
            )

    async def get_state(self, key: StorageKey) -> str | None:
        async with self._session() as session:
            user_id = await self._internal_user_id(session, key)
            if user_id is None:
                return None
            record = await ConversationStateRepository(session).get_for_user(user_id)
            return record.state or None if record is not None else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        async with self._session() as session:
            user_id = await self._internal_user_id(session, key)
            if user_id is None:
                return
            repository = ConversationStateRepository(session)
            current = await repository.get_for_user(user_id)
            if not data:
                # ``FSMContext.clear()`` clears the state and then the data;
                # once both are empty the row has no reason to exist.
                if current is None:
                    return
                if not current.state:
                    await repository.clear(user_id)
                    return
                await repository.set_state(
                    user_id,
                    state=current.state,
                    payload=None,
                    ttl_seconds=self._settings.conversation_state_ttl_seconds,
                )
                return
            await repository.set_state(
                user_id,
                state=current.state if current is not None else "",
                payload={PAYLOAD_KEY: dict(data)},
                ttl_seconds=self._settings.conversation_state_ttl_seconds,
            )

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        async with self._session() as session:
            user_id = await self._internal_user_id(session, key)
            if user_id is None:
                return {}
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
