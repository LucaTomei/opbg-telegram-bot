"""The credential flow lives entirely inside the Telegram conversation.

The password is read once, the message carrying it is deleted immediately and
nothing but the encrypted ciphertext survives.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import callbacks
from app.db.models.users import TelegramUser
from app.db.repositories.accounts import PatientRepository, PortalAccountRepository
from app.db.repositories.base import utc_now
from app.db.repositories.conversations import ConversationStateRepository
from app.db.repositories.users import UserRepository
from app.db.session import Database
from app.domain.enums import PortalAccountStatus, UserStatus
from tests.support.bot import BotHarness
from tests.support.factories import make_account
from tests.support.portal import VALID_EMAIL, VALID_PASSWORD, FakePortal

USER_ID = 730001
CONNECT = callbacks.build(callbacks.ACCOUNT, "conn")


@pytest.fixture
async def member(clean_session: AsyncSession, bot: BotHarness) -> TelegramUser:
    user = await UserRepository(clean_session).create(telegram_id=USER_ID, status=UserStatus.ACTIVE)
    user.consent_version = "1.0"
    user.consented_at = utc_now()
    await clean_session.commit()
    return user


async def run_flow(
    bot: BotHarness,
    *,
    label: str = "Account principale",
    email: str = VALID_EMAIL,
    password: str = VALID_PASSWORD,
) -> None:
    await bot.press(USER_ID, CONNECT)
    await bot.send(USER_ID, label)
    await bot.send(USER_ID, email)
    await bot.send(USER_ID, password)


# --- happy path ---------------------------------------------------------------
async def test_the_bot_asks_for_label_email_and_password(
    bot: BotHarness, member: TelegramUser
) -> None:
    await bot.press(USER_ID, CONNECT)
    assert bot.contains("Dai un nome a questo account")

    bot.reset()
    await bot.send(USER_ID, "Account principale")
    assert bot.contains("email")

    bot.reset()
    await bot.send(USER_ID, VALID_EMAIL)
    assert bot.contains("password")
    assert bot.contains("cancellato subito")


async def test_the_account_is_connected_and_patients_synchronised(
    bot: BotHarness, member: TelegramUser, database: Database
) -> None:
    await run_flow(bot)
    assert bot.contains("collegato")

    async with database.read_session() as session:
        accounts = await PortalAccountRepository(session).list_for_user(member.id)
        assert len(accounts) == 1
        assert accounts[0].status is PortalAccountStatus.ACTIVE
        assert accounts[0].email == VALID_EMAIL
        assert accounts[0].password == VALID_PASSWORD
        patients = await PatientRepository(session).list_for_account(accounts[0].id)
        assert len(patients) == 2


# --- the password must not linger ---------------------------------------------
async def test_the_password_message_is_deleted(bot: BotHarness, member: TelegramUser) -> None:
    await bot.press(USER_ID, CONNECT)
    await bot.send(USER_ID, "Account principale")
    await bot.send(USER_ID, VALID_EMAIL)
    bot.reset()
    await bot.send(USER_ID, VALID_PASSWORD)
    password_message_id = bot.last_sent_message_id

    assert password_message_id in bot.deleted
    assert bot.contains("Messaggio con la password eliminato")


async def test_a_failed_deletion_is_reported_to_the_user(
    bot: BotHarness, member: TelegramUser
) -> None:
    """If Telegram refuses the deletion the user must be told to do it."""
    await bot.press(USER_ID, CONNECT)
    await bot.send(USER_ID, "Account principale")
    await bot.send(USER_ID, VALID_EMAIL)
    bot.reset()
    bot.session.deletion_fails = True
    try:
        await bot.send(USER_ID, VALID_PASSWORD)
    finally:
        bot.session.deletion_fails = False
    assert bot.contains("cancellalo tu dalla chat")


async def test_the_password_never_reaches_the_conversation_state(
    bot: BotHarness, member: TelegramUser, database: Database
) -> None:
    await bot.press(USER_ID, CONNECT)
    await bot.send(USER_ID, "Account principale")
    await bot.send(USER_ID, VALID_EMAIL)

    async with database.read_session() as session:
        state = await ConversationStateRepository(session).get_for_user(member.id)
        assert state is not None
        payload = state.payload or {}
        assert VALID_PASSWORD not in str(payload)

    await bot.send(USER_ID, VALID_PASSWORD)

    async with database.read_session() as session:
        assert await ConversationStateRepository(session).get_for_user(member.id) is None


async def test_the_password_is_never_written_in_clear_to_the_database(
    bot: BotHarness, member: TelegramUser, database: Database
) -> None:
    await run_flow(bot)
    async with database.read_session() as session:
        rows = (
            await session.execute(
                text("SELECT password_ciphertext, email_ciphertext FROM portal_accounts")
            )
        ).all()
        assert rows
        for password, email in rows:
            blob = bytes(password or b"") + bytes(email or b"")
            assert VALID_PASSWORD.encode() not in blob
            assert VALID_EMAIL.encode() not in blob


async def test_the_password_never_appears_in_an_outgoing_message(
    bot: BotHarness, member: TelegramUser
) -> None:
    await run_flow(bot)
    for call in bot.sent:
        assert VALID_PASSWORD not in call.text


# --- failures -----------------------------------------------------------------
async def test_wrong_credentials_are_reported_without_creating_an_account(
    bot: BotHarness, member: TelegramUser, database: Database
) -> None:
    await run_flow(bot, password="password-sbagliata")
    assert bot.contains("Credenziali non valide")
    async with database.read_session() as session:
        assert await PortalAccountRepository(session).count_for_user(member.id) == 0


async def test_an_invalid_email_is_rejected_before_the_password_is_asked(
    bot: BotHarness, member: TelegramUser
) -> None:
    await bot.press(USER_ID, CONNECT)
    await bot.send(USER_ID, "Account principale")
    bot.reset()
    await bot.send(USER_ID, "non-una-email")
    assert not bot.contains("password")
    assert bot.contains("Dato non valido")


async def test_the_flow_can_be_cancelled(
    bot: BotHarness, member: TelegramUser, database: Database
) -> None:
    await bot.press(USER_ID, CONNECT)
    await bot.send(USER_ID, "Account principale")
    bot.reset()
    await bot.send(USER_ID, "/cancel")
    assert bot.contains("annullata")
    async with database.read_session() as session:
        assert await ConversationStateRepository(session).get_for_user(member.id) is None


async def test_a_second_account_with_the_same_email_is_refused(
    bot: BotHarness, member: TelegramUser
) -> None:
    await run_flow(bot)
    bot.reset()
    await run_flow(bot, label="Doppione")
    assert bot.contains("già presente") or bot.contains("già collegato")


# --- re-authentication ---------------------------------------------------------
async def test_reauthentication_asks_only_for_email_and_password(
    bot: BotHarness,
    clean_session: AsyncSession,
    member: TelegramUser,
    database: Database,
    portal: FakePortal,
    bot_portal: FakePortal,
) -> None:
    account = await make_account(clean_session, member, email="vecchia@example.invalid")
    await clean_session.commit()

    await bot.press(USER_ID, callbacks.build(callbacks.ACCOUNT, "reauth", account.id))
    assert bot.contains("email")

    bot.reset()
    await bot.send(USER_ID, VALID_EMAIL)
    await bot.send(USER_ID, VALID_PASSWORD)
    assert bot.contains("Credenziali aggiornate")

    async with database.read_session() as session:
        accounts = await PortalAccountRepository(session).list_for_user(member.id)
        assert len(accounts) == 1
        assert accounts[0].email == VALID_EMAIL
