"""Invite, consent, navigation and authorization through the real dispatcher."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import callbacks, texts
from app.db.models.users import TelegramUser
from app.db.repositories.base import utc_now
from app.db.repositories.users import InviteRepository, UserRepository
from app.db.session import Database
from app.domain.enums import UserRole, UserStatus
from app.security.tokens import generate_invite_code
from tests.support.bot import BotHarness

INVITED_ID = 700001
STRANGER_ID = 700002
ADMIN_ID = 1001


@pytest.fixture
async def harness(bot: BotHarness) -> BotHarness:
    return bot


async def create_invite(session: AsyncSession, admin: TelegramUser) -> str:
    code = generate_invite_code()
    await InviteRepository(session).create(
        created_by_user_id=admin.id, code=code, ttl_hours=24, max_uses=1
    )
    await session.commit()
    return code


@pytest.fixture
async def admin(clean_session: AsyncSession) -> TelegramUser:
    user = await UserRepository(clean_session).upsert_admin(ADMIN_ID, timezone="Europe/Rome")
    user.status = UserStatus.ACTIVE
    user.consent_version = "1.0"
    user.consented_at = utc_now()
    await clean_session.commit()
    return user


async def test_an_unknown_user_is_asked_for_an_invite(harness: BotHarness) -> None:
    await harness.send(STRANGER_ID, "/start")
    assert harness.contains("invito")


async def test_an_unknown_user_cannot_reach_any_feature(harness: BotHarness) -> None:
    await harness.send(STRANGER_ID, texts.BTN_APPOINTMENTS)
    assert harness.contains("privato")
    assert not harness.contains(texts.APPOINTMENTS_TITLE)


async def test_an_invalid_invite_is_refused(harness: BotHarness) -> None:
    await harness.send(STRANGER_ID, "/start AAAA-BBBB-CCCC")
    assert harness.contains(texts.INVITE_INVALID)


async def test_a_valid_invite_leads_to_the_consent_screen(
    harness: BotHarness, clean_session: AsyncSession, admin: TelegramUser
) -> None:
    code = await create_invite(clean_session, admin)
    await harness.send(INVITED_ID, f"/start {code}")
    assert harness.contains(texts.INVITE_ACCEPTED)
    consent = harness.find("Informativa")
    assert texts.CONSENT_ACCEPT in consent.buttons()


async def test_the_menu_is_locked_until_consent_is_given(
    harness: BotHarness, clean_session: AsyncSession, admin: TelegramUser
) -> None:
    code = await create_invite(clean_session, admin)
    await harness.send(INVITED_ID, f"/start {code}")
    harness.reset()
    await harness.send(INVITED_ID, texts.BTN_BOOK)
    assert harness.contains("informativa")


async def test_accepting_the_consent_activates_the_user(
    harness: BotHarness,
    clean_session: AsyncSession,
    admin: TelegramUser,
    database: Database,
) -> None:
    code = await create_invite(clean_session, admin)
    await harness.send(INVITED_ID, f"/start {code}")
    harness.reset()
    await harness.press(INVITED_ID, callbacks.build(callbacks.CONSENT, "accept"))
    assert harness.contains(texts.CONSENT_ACCEPTED)

    async with database.read_session() as session:
        user = await UserRepository(session).get_by_telegram_id(INVITED_ID)
        assert user is not None
        assert user.status is UserStatus.ACTIVE
        assert user.consent_version == "1.0"


async def test_declining_the_consent_keeps_the_user_locked(
    harness: BotHarness,
    clean_session: AsyncSession,
    admin: TelegramUser,
    database: Database,
) -> None:
    code = await create_invite(clean_session, admin)
    await harness.send(INVITED_ID, f"/start {code}")
    await harness.press(INVITED_ID, callbacks.build(callbacks.CONSENT, "decline"))
    assert harness.contains("Senza accettazione")
    async with database.read_session() as session:
        user = await UserRepository(session).get_by_telegram_id(INVITED_ID)
        assert user is not None
        assert user.status is UserStatus.PENDING_CONSENT


async def test_an_invite_cannot_be_reused_by_another_user(
    harness: BotHarness, clean_session: AsyncSession, admin: TelegramUser
) -> None:
    code = await create_invite(clean_session, admin)
    await harness.send(INVITED_ID, f"/start {code}")
    harness.reset()
    await harness.send(STRANGER_ID, f"/start {code}")
    assert harness.contains(texts.INVITE_CONSUMED)


async def test_an_expired_invite_is_refused(
    harness: BotHarness, clean_session: AsyncSession, admin: TelegramUser
) -> None:
    code = generate_invite_code()
    invite = await InviteRepository(clean_session).create(
        created_by_user_id=admin.id, code=code, ttl_hours=1, max_uses=1
    )
    invite.expires_at = utc_now().replace(year=2020)
    await clean_session.commit()
    await harness.send(STRANGER_ID, f"/start {code}")
    assert harness.contains(texts.INVITE_EXPIRED)


async def test_a_revoked_invite_is_refused(
    harness: BotHarness, clean_session: AsyncSession, admin: TelegramUser
) -> None:
    code = generate_invite_code()
    invite = await InviteRepository(clean_session).create(
        created_by_user_id=admin.id, code=code, ttl_hours=24, max_uses=1
    )
    await InviteRepository(clean_session).revoke(invite)
    await clean_session.commit()
    await harness.send(STRANGER_ID, f"/start {code}")
    assert harness.contains(texts.INVITE_CONSUMED)


# --- navigation --------------------------------------------------------------
@pytest.fixture
async def member(clean_session: AsyncSession, admin: TelegramUser) -> TelegramUser:
    user = await UserRepository(clean_session).create(
        telegram_id=INVITED_ID, status=UserStatus.ACTIVE
    )
    user.consent_version = "1.0"
    user.consented_at = utc_now()
    await clean_session.commit()
    return user


async def test_the_main_menu_shows_the_persistent_keyboard(
    harness: BotHarness, member: TelegramUser
) -> None:
    await harness.send(INVITED_ID, "/menu")
    payload = harness.last().payload
    keyboard: Any = payload.get("reply_markup")
    labels = {
        button["text"] if isinstance(button, dict) else button.text
        for row in (keyboard["keyboard"] if isinstance(keyboard, dict) else keyboard.keyboard)
        for button in row
    }
    assert texts.BTN_BOOK in labels
    assert texts.BTN_ADMIN not in labels


async def test_administrators_see_the_administration_entry(
    harness: BotHarness, admin: TelegramUser
) -> None:
    await harness.send(ADMIN_ID, "/menu")
    keyboard: Any = harness.last().payload["reply_markup"]
    labels = {
        button["text"] if isinstance(button, dict) else button.text
        for row in (keyboard["keyboard"] if isinstance(keyboard, dict) else keyboard.keyboard)
        for button in row
    }
    assert texts.BTN_ADMIN in labels


async def test_help_privacy_and_status_are_available(
    harness: BotHarness, member: TelegramUser
) -> None:
    await harness.send(INVITED_ID, "/help")
    assert harness.contains("Guida rapida")
    await harness.send(INVITED_ID, "/privacy")
    assert harness.contains("Privacy")
    await harness.send(INVITED_ID, "/status")
    assert harness.contains("Il tuo profilo")


async def test_cancel_returns_to_the_menu(harness: BotHarness, member: TelegramUser) -> None:
    await harness.send(INVITED_ID, "/cancel")
    assert harness.contains("annullata") or harness.contains("nessuna operazione")


async def test_the_menu_callback_works_from_any_screen(
    harness: BotHarness, member: TelegramUser
) -> None:
    await harness.press(INVITED_ID, callbacks.menu())
    assert harness.contains("Menu principale")


async def test_a_suspended_user_is_blocked(
    harness: BotHarness, clean_session: AsyncSession, member: TelegramUser
) -> None:
    member.status = UserStatus.SUSPENDED
    await clean_session.commit()
    await harness.send(INVITED_ID, texts.BTN_BOOK)
    assert harness.contains("sospeso")


async def test_a_blocked_user_is_refused(
    harness: BotHarness, clean_session: AsyncSession, member: TelegramUser
) -> None:
    member.status = UserStatus.BLOCKED
    await clean_session.commit()
    await harness.send(INVITED_ID, texts.BTN_APPOINTMENTS)
    assert harness.contains("revocato")


async def test_a_regular_user_cannot_open_the_administration(
    harness: BotHarness, member: TelegramUser
) -> None:
    await harness.send(INVITED_ID, texts.BTN_ADMIN)
    assert harness.contains(texts.ADMIN_ONLY)
    assert not harness.contains("Utenti")


async def test_the_administrator_is_upserted_from_the_environment(
    bot: BotHarness, clean_session: AsyncSession
) -> None:
    admins = await bot.container.users.bootstrap_admins(clean_session)
    assert [admin.telegram_id for admin in admins] == [ADMIN_ID]
    assert admins[0].role is UserRole.ADMIN
