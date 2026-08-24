"""Onboarding: invite redemption, consent and the main menu entry point."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import callbacks, texts
from app.bot.keyboards.inline import Entry, build_actions
from app.bot.keyboards.reply import main_keyboard
from app.bot.routers.common import PARSE_MODE, acknowledge, render, show_main_menu
from app.composition import Container
from app.db.models.users import TelegramUser
from app.db.repositories.users import UserRepository
from app.domain.enums import UserRole, UserStatus
from app.domain.errors import (
    AlreadyConsumedError,
    ExpiredError,
    NotFoundError,
)
from app.logging import get_logger

logger = get_logger(__name__)
router = Router(name="start")

ACTION_ACCEPT = "accept"
ACTION_DECLINE = "decline"


def consent_keyboard() -> object:
    return build_actions(
        [
            [
                Entry(texts.CONSENT_ACCEPT, callbacks.build(callbacks.CONSENT, ACTION_ACCEPT)),
                Entry(texts.CONSENT_DECLINE, callbacks.build(callbacks.CONSENT, ACTION_DECLINE)),
            ]
        ],
        show_menu=False,
    )


async def _show_consent(message: Message, container: Container) -> None:
    await message.answer(
        texts.CONSENT_TEXT.format(version=container.settings.consent_version),
        reply_markup=consent_keyboard(),  # type: ignore[arg-type]
        parse_mode=PARSE_MODE,
    )


@router.message(CommandStart(deep_link=True, ignore_case=True))
@router.message(CommandStart())
async def handle_start(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    container: Container,
    user: TelegramUser | None,
) -> None:
    """Handle ``/start`` with or without an invite code."""
    code = (command.args or "").strip()
    if user is not None and user.status is UserStatus.ACTIVE and user.has_consented:
        await message.answer(
            texts.MAIN_MENU,
            reply_markup=main_keyboard(is_admin=user.is_admin),
            parse_mode=PARSE_MODE,
        )
        return

    if user is not None and user.role is UserRole.ADMIN and not user.has_consented:
        await _show_consent(message, container)
        return

    if not code:
        if user is not None and not user.has_consented:
            await _show_consent(message, container)
            return
        await message.answer(texts.WELCOME_NEEDS_INVITE, parse_mode=PARSE_MODE)
        return

    try:
        result = await container.users.redeem_invite(
            session, telegram_id=message.from_user.id if message.from_user else 0, code=code
        )
    except NotFoundError:
        await message.answer(texts.INVITE_INVALID)
        return
    except ExpiredError:
        await message.answer(texts.INVITE_EXPIRED)
        return
    except AlreadyConsumedError:
        await message.answer(texts.INVITE_CONSUMED)
        return

    if message.from_user is not None:
        await container.users.update_profile(
            session,
            result.user,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
        )
    logger.info(
        "invite redeemed",
        extra={"event_code": "INVITE_REDEEMED", "new_user": result.newly_registered},
    )
    await message.answer(texts.INVITE_ACCEPTED)
    if result.user.has_consented and result.user.status is UserStatus.ACTIVE:
        await message.answer(
            texts.MAIN_MENU,
            reply_markup=main_keyboard(is_admin=result.user.is_admin),
            parse_mode=PARSE_MODE,
        )
        return
    await _show_consent(message, container)


@router.callback_query(F.data == callbacks.build(callbacks.CONSENT, ACTION_ACCEPT))
async def accept_consent(
    query: CallbackQuery,
    session: AsyncSession,
    container: Container,
) -> None:
    """Record the consent version and unlock the main menu."""
    await acknowledge(query)
    telegram_id = query.from_user.id
    user = await UserRepository(session).get_by_telegram_id(telegram_id)
    if user is None:
        await render(query, texts.NOT_AUTHORIZED)
        return
    await container.users.accept_consent(session, user)
    await render(query, texts.CONSENT_ACCEPTED)
    await show_main_menu(query, user)


@router.callback_query(F.data == callbacks.build(callbacks.CONSENT, ACTION_DECLINE))
async def decline_consent(query: CallbackQuery) -> None:
    await acknowledge(query)
    await render(query, texts.CONSENT_DECLINED)


@router.message(Command("privacy"))
async def show_privacy(message: Message) -> None:
    await message.answer(texts.PRIVACY_SHORT, parse_mode=PARSE_MODE)


@router.message(Command("help"))
async def show_help(message: Message, user: TelegramUser | None) -> None:
    await message.answer(
        texts.HELP_TEXT,
        reply_markup=main_keyboard(is_admin=user is not None and user.is_admin),
        parse_mode=PARSE_MODE,
    )
