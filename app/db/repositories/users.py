"""Repositories for Telegram users, invites and redemptions."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import Select, or_, select

from app.db.base import blind_index_for
from app.db.models.users import (
    InviteCode,
    InviteRedemption,
    TelegramUser,
    UserSettingsAudit,
)
from app.db.repositories.base import BaseRepository, Page, utc_now
from app.domain.enums import UserRole, UserStatus
from app.security.tokens import hash_invite_code


class UserRepository(BaseRepository[TelegramUser]):
    model = TelegramUser

    async def get_by_telegram_id(self, telegram_id: int) -> TelegramUser | None:
        return await self.one_or_none(
            select(TelegramUser).where(TelegramUser.telegram_id == telegram_id)
        )

    async def create(
        self,
        *,
        telegram_id: int,
        role: UserRole = UserRole.USER,
        status: UserStatus = UserStatus.PENDING_CONSENT,
        timezone: str = "Europe/Rome",
        page_size: int = 6,
        max_slots_shown: int = 40,
    ) -> TelegramUser:
        """Create a user with the deployment defaults already applied."""
        user = TelegramUser(
            telegram_id=telegram_id,
            role=role,
            status=status,
            timezone=timezone,
            page_size=page_size,
            max_slots_shown=max_slots_shown,
        )
        return await self.add(user)

    async def upsert_admin(
        self,
        telegram_id: int,
        *,
        timezone: str,
        page_size: int = 6,
        max_slots_shown: int = 40,
    ) -> TelegramUser:
        """Create or promote the administrators declared in the environment."""
        user = await self.get_by_telegram_id(telegram_id)
        if user is None:
            return await self.create(
                telegram_id=telegram_id,
                role=UserRole.ADMIN,
                status=UserStatus.PENDING_CONSENT,
                timezone=timezone,
                page_size=page_size,
                max_slots_shown=max_slots_shown,
            )
        if user.role is not UserRole.ADMIN:
            user.role = UserRole.ADMIN
        if user.status in (UserStatus.SUSPENDED, UserStatus.BLOCKED):
            user.status = UserStatus.ACTIVE
        user.deleted_at = None
        await self.flush()
        return user

    async def demote_admins_except(self, telegram_ids: Sequence[int]) -> int:
        """Revoke the admin role from users no longer listed in the environment."""
        statement = select(TelegramUser).where(TelegramUser.role == UserRole.ADMIN)
        if telegram_ids:
            statement = statement.where(TelegramUser.telegram_id.notin_(list(telegram_ids)))
        demoted = 0
        for user in await self.session.scalars(statement):
            user.role = UserRole.USER
            demoted += 1
        await self.flush()
        return demoted

    async def touch(self, user: TelegramUser) -> None:
        user.last_seen_at = utc_now()
        await self.flush()

    def _list_statement(
        self,
        *,
        status: UserStatus | None,
        role: UserRole | None,
        telegram_id: int | None,
    ) -> Select[tuple[TelegramUser]]:
        statement = select(TelegramUser).order_by(TelegramUser.created_at.desc())
        if status is not None:
            statement = statement.where(TelegramUser.status == status)
        if role is not None:
            statement = statement.where(TelegramUser.role == role)
        if telegram_id is not None:
            statement = statement.where(TelegramUser.telegram_id == telegram_id)
        return statement

    async def list_users(
        self,
        *,
        page: int,
        page_size: int,
        status: UserStatus | None = None,
        role: UserRole | None = None,
        telegram_id: int | None = None,
    ) -> Page[TelegramUser]:
        return await self.paginate(
            self._list_statement(status=status, role=role, telegram_id=telegram_id),
            page=page,
            page_size=page_size,
        )

    async def count_by_status(self) -> dict[UserStatus, int]:
        counts: dict[UserStatus, int] = {}
        for status in UserStatus:
            statement = select(TelegramUser).where(TelegramUser.status == status)
            counts[status] = await self.count(statement)
        return counts

    async def record_setting_change(
        self, user: TelegramUser, *, setting: str, old_value: str | None, new_value: str | None
    ) -> None:
        self.session.add(
            UserSettingsAudit(
                telegram_user_id=user.id,
                setting=setting[:64],
                old_value=None if old_value is None else old_value[:128],
                new_value=None if new_value is None else new_value[:128],
            )
        )
        await self.flush()


class InviteRepository(BaseRepository[InviteCode]):
    model = InviteCode

    async def create(
        self,
        *,
        created_by_user_id: uuid.UUID,
        code: str,
        ttl_hours: int,
        max_uses: int,
        note: str | None = None,
    ) -> InviteCode:
        invite = InviteCode(
            code_hash=hash_invite_code(code),
            created_by_user_id=created_by_user_id,
            expires_at=utc_now() + timedelta(hours=ttl_hours),
            max_uses=max(max_uses, 1),
            note=note,
        )
        return await self.add(invite)

    async def get_by_code(self, code: str) -> InviteCode | None:
        try:
            code_hash = hash_invite_code(code)
        except ValueError:
            return None
        return await self.one_or_none(select(InviteCode).where(InviteCode.code_hash == code_hash))

    async def redeem(self, invite: InviteCode, user: TelegramUser, now: datetime) -> None:
        invite.used_count += 1
        self.session.add(
            InviteRedemption(invite_id=invite.id, telegram_user_id=user.id, redeemed_at=now)
        )
        await self.flush()

    async def revoke(self, invite: InviteCode) -> None:
        if invite.revoked_at is None:
            invite.revoked_at = utc_now()
        await self.flush()

    async def already_redeemed(self, invite: InviteCode, user: TelegramUser) -> bool:
        existing = await self.one_or_none(
            select(InviteRedemption).where(
                InviteRedemption.invite_id == invite.id,
                InviteRedemption.telegram_user_id == user.id,
            )
        )
        return existing is not None

    async def list_invites(
        self, *, page: int, page_size: int, only_active: bool = False
    ) -> Page[InviteCode]:
        statement = select(InviteCode).order_by(InviteCode.created_at.desc())
        if only_active:
            statement = statement.where(
                InviteCode.revoked_at.is_(None),
                InviteCode.expires_at > utc_now(),
                InviteCode.used_count < InviteCode.max_uses,
            )
        return await self.paginate(statement, page=page, page_size=page_size)

    async def redemptions_for(self, invite_id: uuid.UUID) -> list[InviteRedemption]:
        result = await self.session.scalars(
            select(InviteRedemption).where(InviteRedemption.invite_id == invite_id)
        )
        return list(result)

    async def purge_expired(self, *, older_than: datetime) -> int:
        statement = select(InviteCode).where(
            or_(InviteCode.expires_at < older_than, InviteCode.revoked_at.isnot(None)),
            InviteCode.created_at < older_than,
        )
        removed = 0
        for invite in await self.session.scalars(statement):
            redemptions = await self.redemptions_for(invite.id)
            if redemptions:
                continue
            await self.session.delete(invite)
            removed += 1
        await self.flush()
        return removed


__all__ = ["InviteRepository", "UserRepository", "blind_index_for"]
