"""Repositories for conversation state, drafts, nonces and callback idempotency."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models.conversations import (
    BookingDraft,
    CallbackConsumption,
    ConfirmationNonce,
    ConversationState,
)
from app.db.repositories.base import BaseRepository, utc_now
from app.domain.enums import BookingDraftStatus, ConfirmationAction
from app.security.tokens import hash_token


class ConversationStateRepository(BaseRepository[ConversationState]):
    model = ConversationState

    async def get_for_user(self, user_id: uuid.UUID) -> ConversationState | None:
        state = await self.session.get(ConversationState, user_id)
        if state is None:
            return None
        if state.expires_at <= utc_now():
            await self.session.delete(state)
            await self.flush()
            return None
        return state

    async def set_state(
        self,
        user_id: uuid.UUID,
        *,
        state: str,
        payload: dict[str, Any] | None = None,
        ttl_seconds: int,
    ) -> ConversationState:
        record = await self.session.get(ConversationState, user_id)
        expires_at = utc_now() + timedelta(seconds=ttl_seconds)
        if record is None:
            record = ConversationState(telegram_user_id=user_id, state=state, expires_at=expires_at)
            record.payload = payload
            self.session.add(record)
        else:
            record.state = state
            record.payload = payload
            record.expires_at = expires_at
        await self.flush()
        return record

    async def clear(self, user_id: uuid.UUID) -> None:
        record = await self.session.get(ConversationState, user_id)
        if record is not None:
            await self.session.delete(record)
            await self.flush()

    async def purge_expired(self, *, before: datetime) -> int:
        statement = select(ConversationState).where(ConversationState.expires_at < before)
        removed = 0
        for record in await self.session.scalars(statement):
            await self.session.delete(record)
            removed += 1
        await self.flush()
        return removed


class BookingDraftRepository(BaseRepository[BookingDraft]):
    model = BookingDraft

    async def create(self, *, user_id: uuid.UUID, ttl_seconds: int) -> BookingDraft:
        draft = BookingDraft(
            telegram_user_id=user_id,
            expires_at=utc_now() + timedelta(seconds=ttl_seconds),
        )
        return await self.add(draft)

    async def get_owned(self, draft_id: uuid.UUID, user_id: uuid.UUID) -> BookingDraft | None:
        draft = await self.one_or_none(
            select(BookingDraft).where(
                BookingDraft.id == draft_id,
                BookingDraft.telegram_user_id == user_id,
            )
        )
        if draft is None or draft.expires_at <= utc_now():
            return None
        return draft

    async def latest_open(self, user_id: uuid.UUID) -> BookingDraft | None:
        return await self.one_or_none(
            select(BookingDraft)
            .where(
                BookingDraft.telegram_user_id == user_id,
                BookingDraft.status == BookingDraftStatus.OPEN,
                BookingDraft.expires_at > utc_now(),
            )
            .order_by(BookingDraft.created_at.desc())
            .limit(1)
        )

    async def extend(self, draft: BookingDraft, *, ttl_seconds: int) -> None:
        draft.expires_at = utc_now() + timedelta(seconds=ttl_seconds)
        await self.flush()

    async def close(self, draft: BookingDraft, status: BookingDraftStatus) -> None:
        draft.status = status
        await self.flush()

    async def abandon_open_for_user(self, user_id: uuid.UUID) -> int:
        statement = select(BookingDraft).where(
            BookingDraft.telegram_user_id == user_id,
            BookingDraft.status == BookingDraftStatus.OPEN,
        )
        closed = 0
        for draft in await self.session.scalars(statement):
            draft.status = BookingDraftStatus.ABANDONED
            closed += 1
        await self.flush()
        return closed

    async def purge_expired(self, *, before: datetime) -> int:
        statement = select(BookingDraft).where(BookingDraft.expires_at < before)
        removed = 0
        for draft in await self.session.scalars(statement):
            await self.session.delete(draft)
            removed += 1
        await self.flush()
        return removed


class ConfirmationNonceRepository(BaseRepository[ConfirmationNonce]):
    model = ConfirmationNonce

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        action: ConfirmationAction,
        target_type: str,
        target_id: str,
        nonce: str,
        context_hash: bytes,
        ttl_seconds: int,
        reason: str | None = None,
    ) -> ConfirmationNonce:
        record = ConfirmationNonce(
            telegram_user_id=user_id,
            action_type=action,
            target_type=target_type[:32],
            target_id=target_id[:64],
            nonce_hash=hash_token(nonce),
            context_hash=context_hash,
            expires_at=utc_now() + timedelta(seconds=ttl_seconds),
            reason=None if reason is None else reason[:255],
        )
        return await self.add(record)

    async def get_by_nonce(self, nonce: str) -> ConfirmationNonce | None:
        return await self.one_or_none(
            select(ConfirmationNonce).where(ConfirmationNonce.nonce_hash == hash_token(nonce))
        )

    async def consume(self, record: ConfirmationNonce) -> bool:
        """Consume the nonce once; a repeated call returns ``False``."""
        if record.consumed_at is not None:
            return False
        record.consumed_at = utc_now()
        await self.flush()
        return True

    async def invalidate_for_target(
        self, *, user_id: uuid.UUID, action: ConfirmationAction, target_id: str
    ) -> int:
        statement = select(ConfirmationNonce).where(
            ConfirmationNonce.telegram_user_id == user_id,
            ConfirmationNonce.action_type == action,
            ConfirmationNonce.target_id == target_id[:64],
            ConfirmationNonce.consumed_at.is_(None),
        )
        count = 0
        now = utc_now()
        for record in await self.session.scalars(statement):
            record.consumed_at = now
            count += 1
        await self.flush()
        return count

    async def purge_expired(self, *, before: datetime) -> int:
        statement = select(ConfirmationNonce).where(ConfirmationNonce.expires_at < before)
        removed = 0
        for record in await self.session.scalars(statement):
            await self.session.delete(record)
            removed += 1
        await self.flush()
        return removed


class CallbackConsumptionRepository(BaseRepository[CallbackConsumption]):
    model = CallbackConsumption

    async def claim(self, *, dedup_key: bytes, user_id: uuid.UUID | None, ttl_seconds: int) -> bool:
        """Return ``True`` when this update is seen for the first time."""
        record = CallbackConsumption(
            dedup_key=dedup_key,
            telegram_user_id=user_id,
            expires_at=utc_now() + timedelta(seconds=ttl_seconds),
        )
        self.session.add(record)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            return False
        return True

    async def purge_expired(self, *, before: datetime) -> int:
        statement = select(CallbackConsumption).where(CallbackConsumption.expires_at < before)
        removed = 0
        for record in await self.session.scalars(statement):
            await self.session.delete(record)
            removed += 1
        await self.flush()
        return removed
