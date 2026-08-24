"""Main menu, help, status and cancellation."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import callbacks, texts
from app.bot.keyboards.reply import main_keyboard
from app.bot.routers.common import PARSE_MODE, acknowledge, render, show_main_menu
from app.db.models.users import TelegramUser
from app.db.repositories.accounts import PortalAccountRepository
from app.db.repositories.bookings import AppointmentRepository
from app.db.repositories.conversations import BookingDraftRepository
from app.db.repositories.watches import WatchRepository
from app.domain.enums import UserRole, WatchStatus

router = Router(name="menu")


@router.message(Command("menu"))
@router.message(F.text == texts.BTN_MENU)
async def open_menu(message: Message, user: TelegramUser | None, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        texts.MAIN_MENU,
        reply_markup=main_keyboard(is_admin=user is not None and user.is_admin),
        parse_mode=PARSE_MODE,
    )


@router.message(F.text == texts.BTN_HELP)
async def open_help(message: Message, user: TelegramUser | None) -> None:
    await message.answer(
        texts.HELP_TEXT,
        reply_markup=main_keyboard(is_admin=user is not None and user.is_admin),
        parse_mode=PARSE_MODE,
    )


@router.message(Command("cancel"))
@router.message(F.text == texts.BTN_CANCEL)
async def cancel_flow(
    message: Message,
    user: TelegramUser | None,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """``/cancel`` aborts only the current conversation, never a saved object."""
    current = await state.get_state()
    await state.clear()
    if user is not None:
        await BookingDraftRepository(session).abandon_open_for_user(user.id)
    await message.answer(
        texts.CANCELLED if current else texts.NOTHING_TO_CANCEL,
        reply_markup=main_keyboard(is_admin=user is not None and user.is_admin),
    )


@router.message(Command("status"))
async def show_status(message: Message, user: TelegramUser | None, session: AsyncSession) -> None:
    """Show a compact snapshot of the user profile."""
    if user is None:
        await message.answer(texts.NOT_AUTHORIZED)
        return
    accounts = await PortalAccountRepository(session).count_for_user(user.id)
    watches = await WatchRepository(session).list_for_user(
        user.id, page=0, page_size=1, status=WatchStatus.ACTIVE
    )
    appointments = await AppointmentRepository(session).list_upcoming(user.id, page=0, page_size=1)
    quiet = (
        f"{user.quiet_hours_start.strftime('%H:%M')}–{user.quiet_hours_end.strftime('%H:%M')}"
        if user.quiet_hours_start and user.quiet_hours_end
        else "non impostati"
    )
    await message.answer(
        texts.STATUS_TEMPLATE.format(
            status=user.status.value.lower(),
            role="amministratore" if user.role is UserRole.ADMIN else "utente",
            accounts=accounts,
            watches=watches.total,
            appointments=appointments.total,
            notifications="attive" if user.notifications_enabled else "disattivate",
            quiet=quiet,
            consent=user.consent_version or "non accettata",
        ),
        reply_markup=main_keyboard(is_admin=user.is_admin),
        parse_mode=PARSE_MODE,
    )


@router.callback_query(F.data == callbacks.menu())
async def menu_callback(query: CallbackQuery, user: TelegramUser | None, state: FSMContext) -> None:
    await acknowledge(query)
    await state.clear()
    await show_main_menu(query, user)


@router.callback_query(F.data == callbacks.cancel())
async def cancel_callback(
    query: CallbackQuery,
    user: TelegramUser | None,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    await acknowledge(query)
    await state.clear()
    if user is not None:
        await BookingDraftRepository(session).abandon_open_for_user(user.id)
    await render(query, texts.CANCELLED)
    await show_main_menu(query, user)


@router.callback_query(F.data == callbacks.noop())
async def noop_callback(query: CallbackQuery) -> None:
    """The page indicator is a button, so it must answer to avoid a spinner."""
    await acknowledge(query)
