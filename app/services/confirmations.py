"""Fresh, expiring double confirmations for every destructive action.

A confirmation is a two-step handshake: the first step shows the summary and
issues a nonce bound to the user, the action, the target and a context hash;
the second step consumes that nonce exactly once.  A repeated Telegram callback
therefore cannot trigger a second mutation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models.conversations import ConfirmationNonce
from app.db.models.users import TelegramUser
from app.db.repositories.base import utc_now
from app.db.repositories.conversations import ConfirmationNonceRepository
from app.domain.enums import ConfirmationAction
from app.domain.errors import (
    AlreadyConsumedError,
    ExpiredError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.security.tokens import generate_nonce, hash_context

MIN_REASON_LENGTH = 5


@dataclass(frozen=True, slots=True)
class Confirmation:
    """A pending confirmation handed to the user interface."""

    nonce: str
    record_id: uuid.UUID
    expires_in_seconds: int


class ConfirmationService:
    """Issues and validates single-use confirmation nonces."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def issue(
        self,
        session: AsyncSession,
        *,
        user: TelegramUser,
        action: ConfirmationAction,
        target_type: str,
        target_id: str,
        context_parts: tuple[str, ...],
        reason: str | None = None,
    ) -> Confirmation:
        """Create a fresh confirmation, invalidating any previous one."""
        repository = ConfirmationNonceRepository(session)
        await repository.invalidate_for_target(user_id=user.id, action=action, target_id=target_id)
        nonce = generate_nonce()
        record = await repository.create(
            user_id=user.id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            nonce=nonce,
            context_hash=hash_context(*context_parts),
            ttl_seconds=self._settings.destructive_confirmation_ttl_seconds,
            reason=reason,
        )
        return Confirmation(
            nonce=nonce,
            record_id=record.id,
            expires_in_seconds=self._settings.destructive_confirmation_ttl_seconds,
        )

    async def consume(
        self,
        session: AsyncSession,
        *,
        user: TelegramUser,
        action: ConfirmationAction,
        nonce: str,
        context_parts: tuple[str, ...],
    ) -> ConfirmationNonce:
        """Validate and burn a confirmation nonce.

        Ownership, action, expiry and the context hash are all re-checked
        against the database: nothing is trusted from the callback payload.
        """
        repository = ConfirmationNonceRepository(session)
        record = await repository.get_by_nonce(nonce)
        if record is None:
            raise NotFoundError("confirmation is unknown")
        if record.telegram_user_id != user.id:
            raise PermissionDeniedError("confirmation belongs to another user")
        if record.action_type is not action:
            raise PermissionDeniedError("confirmation was issued for another action")
        if record.expires_at <= utc_now():
            raise ExpiredError("confirmation expired")
        if record.context_hash != hash_context(*context_parts):
            raise PermissionDeniedError("the confirmed context changed")
        if not await repository.consume(record):
            raise AlreadyConsumedError("confirmation was already used")
        return record

    @staticmethod
    def validate_reason(reason: str) -> str:
        """Administrative actions on other users always require a real reason."""
        cleaned = " ".join(reason.strip().split())
        if len(cleaned) < MIN_REASON_LENGTH:
            raise ValidationError("a reason of at least 5 characters is required")
        return cleaned[:512]
