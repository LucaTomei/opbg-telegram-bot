"""User lifecycle: bootstrap, invites, consent, settings and data deletion."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models.users import TelegramUser
from app.db.repositories.accounts import PortalAccountRepository
from app.db.repositories.base import utc_now
from app.db.repositories.bookings import AppointmentRepository
from app.db.repositories.conversations import (
    BookingDraftRepository,
    ConversationStateRepository,
)
from app.db.repositories.users import InviteRepository, UserRepository
from app.db.repositories.watches import WatchRepository
from app.domain.enums import UserRole, UserStatus, WatchStatus
from app.domain.errors import (
    AlreadyConsumedError,
    ExpiredError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.logging import get_logger
from app.security.tokens import generate_invite_code

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class UserPreferences:
    """Per-deployment defaults applied when a user is first registered."""

    timezone: str
    page_size: int
    max_slots_shown: int


@dataclass(frozen=True, slots=True)
class InviteRedemptionResult:
    """Outcome of redeeming an invite code."""

    user: TelegramUser
    newly_registered: bool


class UserService:
    """Registration, consent and per-user settings."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def default_preferences(self) -> UserPreferences:
        """Deployment defaults applied to a newly registered user."""
        return UserPreferences(
            timezone=self._settings.app_timezone,
            page_size=self._settings.page_size,
            max_slots_shown=self._settings.max_slots_shown,
        )

    async def bootstrap_admins(self, session: AsyncSession) -> list[TelegramUser]:
        """Upsert the administrators declared in ``TELEGRAM_ADMIN_IDS``."""
        users = UserRepository(session)
        admins: list[TelegramUser] = []
        defaults = self.default_preferences()
        for telegram_id in self._settings.admin_telegram_ids:
            admins.append(
                await users.upsert_admin(
                    telegram_id,
                    timezone=defaults.timezone,
                    page_size=defaults.page_size,
                    max_slots_shown=defaults.max_slots_shown,
                )
            )
        await users.demote_admins_except(self._settings.admin_telegram_ids)
        logger.info(
            "administrators synchronised",
            extra={"event_code": "ADMIN_BOOTSTRAP", "count": len(admins)},
        )
        return admins

    async def get_or_none(self, session: AsyncSession, telegram_id: int) -> TelegramUser | None:
        return await UserRepository(session).get_by_telegram_id(telegram_id)

    async def require_user(self, session: AsyncSession, telegram_id: int) -> TelegramUser:
        user = await self.get_or_none(session, telegram_id)
        if user is None or user.status is UserStatus.DELETED:
            raise PermissionDeniedError("user is not registered")
        if user.status is UserStatus.BLOCKED:
            raise PermissionDeniedError("user is blocked")
        return user

    async def update_profile(
        self,
        session: AsyncSession,
        user: TelegramUser,
        *,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
    ) -> None:
        """Refresh the encrypted Telegram profile snapshot."""
        users = UserRepository(session)
        user.username = username
        user.first_name = first_name
        user.last_name = last_name
        await users.touch(user)

    async def redeem_invite(
        self, session: AsyncSession, *, telegram_id: int, code: str
    ) -> InviteRedemptionResult:
        """Validate an invite code and register the user as pending consent."""
        invites = InviteRepository(session)
        users = UserRepository(session)
        invite = await invites.get_by_code(code)
        if invite is None:
            raise NotFoundError("invite code is unknown")
        now = utc_now()
        if invite.revoked_at is not None:
            raise AlreadyConsumedError("invite code was revoked")
        if invite.expires_at <= now:
            raise ExpiredError("invite code expired")

        user = await users.get_by_telegram_id(telegram_id)
        if user is not None and await invites.already_redeemed(invite, user):
            return InviteRedemptionResult(user=user, newly_registered=False)
        if invite.used_count >= invite.max_uses:
            raise AlreadyConsumedError("invite code has no remaining uses")

        newly_registered = False
        if user is None:
            defaults = self.default_preferences()
            user = await users.create(
                telegram_id=telegram_id,
                role=UserRole.USER,
                status=UserStatus.PENDING_CONSENT,
                timezone=defaults.timezone,
                page_size=defaults.page_size,
                max_slots_shown=defaults.max_slots_shown,
            )
            newly_registered = True
        elif user.status is UserStatus.DELETED:
            user.status = UserStatus.PENDING_CONSENT
            user.deleted_at = None
            newly_registered = True
        await invites.redeem(invite, user, now)
        return InviteRedemptionResult(user=user, newly_registered=newly_registered)

    async def accept_consent(self, session: AsyncSession, user: TelegramUser) -> None:
        users = UserRepository(session)
        user.consent_version = self._settings.consent_version
        user.consented_at = utc_now()
        if user.status is UserStatus.PENDING_CONSENT:
            user.status = UserStatus.ACTIVE
        await users.flush()

    async def set_notifications(
        self, session: AsyncSession, user: TelegramUser, *, enabled: bool
    ) -> None:
        users = UserRepository(session)
        old = str(user.notifications_enabled)
        user.notifications_enabled = enabled
        await users.record_setting_change(
            user, setting="notifications_enabled", old_value=old, new_value=str(enabled)
        )

    async def set_quiet_hours(
        self,
        session: AsyncSession,
        user: TelegramUser,
        *,
        start: time | None,
        end: time | None,
    ) -> None:
        if (start is None) != (end is None):
            raise ValidationError("quiet hours require both a start and an end")
        users = UserRepository(session)
        old = f"{user.quiet_hours_start}-{user.quiet_hours_end}"
        user.quiet_hours_start = start
        user.quiet_hours_end = end
        await users.record_setting_change(
            user, setting="quiet_hours", old_value=old, new_value=f"{start}-{end}"
        )

    async def set_page_size(
        self, session: AsyncSession, user: TelegramUser, *, page_size: int
    ) -> None:
        if not 3 <= page_size <= 20:
            raise ValidationError("page size must be between 3 and 20")
        users = UserRepository(session)
        old = str(user.page_size)
        user.page_size = page_size
        await users.record_setting_change(
            user, setting="page_size", old_value=old, new_value=str(page_size)
        )

    async def set_max_slots(
        self, session: AsyncSession, user: TelegramUser, *, max_slots: int
    ) -> None:
        if not 1 <= max_slots <= 200:
            raise ValidationError("the slot limit must be between 1 and 200")
        users = UserRepository(session)
        old = str(user.max_slots_shown)
        user.max_slots_shown = max_slots
        await users.record_setting_change(
            user, setting="max_slots_shown", old_value=old, new_value=str(max_slots)
        )

    def export_settings(self, user: TelegramUser) -> dict[str, object]:
        """Export the non-sensitive settings of a user."""
        return {
            "timezone": user.timezone,
            "notifications_enabled": user.notifications_enabled,
            "quiet_hours_start": None
            if user.quiet_hours_start is None
            else user.quiet_hours_start.strftime("%H:%M"),
            "quiet_hours_end": None
            if user.quiet_hours_end is None
            else user.quiet_hours_end.strftime("%H:%M"),
            "page_size": user.page_size,
            "max_slots_shown": user.max_slots_shown,
            "consent_version": user.consent_version,
            "consented_at": None if user.consented_at is None else user.consented_at.isoformat(),
            "role": user.role.value,
            "status": user.status.value,
        }

    async def delete_all_data(self, session: AsyncSession, user: TelegramUser) -> dict[str, int]:
        """Crypto-erase every personal datum belonging to ``user``.

        Booking attempts and the administrative audit trail are preserved, but
        every credential, cookie, patient record and cached appointment is
        removed and the account rows are soft deleted.
        """
        accounts_repo = PortalAccountRepository(session)
        watches = WatchRepository(session)
        drafts = BookingDraftRepository(session)
        states = ConversationStateRepository(session)
        appointments = AppointmentRepository(session)
        users = UserRepository(session)

        removed = {"accounts": 0, "watches": 0, "appointments": 0}
        for account in await accounts_repo.list_for_user(user.id):
            for watch in (
                await watches.list_all(page=0, page_size=200, account_id=account.id)
            ).items:
                await watches.soft_delete(watch)
                removed["watches"] += 1
            page = await appointments.list_all(page=0, page_size=500, account_id=account.id)
            for appointment in page.items:
                await session.delete(appointment)
                removed["appointments"] += 1
            await accounts_repo.soft_delete(account)
            removed["accounts"] += 1

        await drafts.abandon_open_for_user(user.id)
        await states.clear(user.id)

        user.username = None
        user.first_name = None
        user.last_name = None
        user.status = UserStatus.DELETED
        user.deleted_at = utc_now()
        await users.flush()
        logger.info(
            "user data deleted",
            extra={"event_code": "USER_DATA_DELETED", "user_id": user.id.hex, **removed},
        )
        return removed


class InviteService:
    """Invite generation and revocation, reserved to administrators."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def create(
        self,
        session: AsyncSession,
        *,
        admin: TelegramUser,
        max_uses: int = 1,
        ttl_hours: int | None = None,
        note: str | None = None,
    ) -> tuple[str, uuid.UUID]:
        """Create an invite and return the plaintext code exactly once."""
        if admin.role is not UserRole.ADMIN:
            raise PermissionDeniedError("only administrators can create invites")
        if not 1 <= max_uses <= 50:
            raise ValidationError("an invite may allow between 1 and 50 uses")
        code = generate_invite_code()
        invite = await InviteRepository(session).create(
            created_by_user_id=admin.id,
            code=code,
            ttl_hours=ttl_hours or self._settings.invite_ttl_hours,
            max_uses=max_uses,
            note=note,
        )
        return code, invite.id

    async def revoke(
        self, session: AsyncSession, *, admin: TelegramUser, invite_id: uuid.UUID
    ) -> None:
        if admin.role is not UserRole.ADMIN:
            raise PermissionDeniedError("only administrators can revoke invites")
        invites = InviteRepository(session)
        invite = await invites.get(invite_id)
        if invite is None:
            raise NotFoundError("invite not found")
        await invites.revoke(invite)


async def suspend_user_watches(session: AsyncSession, user_id: uuid.UUID, *, reason: str) -> int:
    """Pause every active monitor owned by a user."""
    watches = WatchRepository(session)
    page = await watches.list_all(page=0, page_size=500, user_id=user_id, status=WatchStatus.ACTIVE)
    for watch in page.items:
        await watches.set_status(watch, WatchStatus.PAUSED, reason=reason)
    return len(page.items)
