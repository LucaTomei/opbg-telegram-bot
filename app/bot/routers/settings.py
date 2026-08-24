"""User settings: notifications, quiet hours, PDF delivery, privacy and deletion."""

from __future__ import annotations

from datetime import time

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import callbacks, texts
from app.bot.keyboards.inline import Entry, build_actions
from app.bot.presenters.formatting import escape
from app.bot.routers.common import acknowledge, render, send_new
from app.composition import Container
from app.db.models.users import TelegramUser
from app.domain.enums import ConfirmationAction
from app.domain.errors import ValidationError
from app.logging import get_logger

logger = get_logger(__name__)
router = Router(name="settings")

NS = callbacks.SETTINGS
A_ROOT = "root"
A_NOTIF = "notif"
A_QUIET = "quiet"
A_PAGE = "psize"
A_SLOTS = "slots"
A_PRIVACY = "priv"
A_EXPORT = "export"
A_DELETE = "del"
A_DELETE_OK = "delok"


class SettingsStates(StatesGroup):
    """Free-text steps of the settings screens."""

    waiting_quiet_hours = State()
    waiting_page_size = State()
    waiting_max_slots = State()


def _summary(user: TelegramUser) -> str:
    quiet = (
        f"{user.quiet_hours_start.strftime('%H:%M')}–{user.quiet_hours_end.strftime('%H:%M')}"
        if user.quiet_hours_start and user.quiet_hours_end
        else "non impostati"
    )
    return "\n".join(
        [
            texts.SETTINGS_TITLE,
            "",
            f"Fuso orario: <b>{escape(user.timezone)}</b> (informativo)",
            "Notifiche globali: <b>"
            + ("attive" if user.notifications_enabled else "disattivate")
            + "</b>",
            f"Orari silenziosi: <b>{quiet}</b>",
            f"Voci per pagina: <b>{user.page_size}</b>",
            f"Proposte massime: <b>{user.max_slots_shown}</b>",
            f"Informativa accettata: <b>{user.consent_version or 'non accettata'}</b>",
        ]
    )


def _root_keyboard(user: TelegramUser) -> object:
    return build_actions(
        [
            [
                Entry(
                    "🔕 Disattiva notifiche"
                    if user.notifications_enabled
                    else "🔔 Attiva notifiche",
                    callbacks.build(NS, A_NOTIF),
                ),
                Entry("🌙 Orari silenziosi", callbacks.build(NS, A_QUIET)),
            ],
            [Entry("🔢 Voci per pagina", callbacks.build(NS, A_PAGE))],
            [
                Entry("📋 Proposte massime", callbacks.build(NS, A_SLOTS)),
                Entry("🔒 Privacy", callbacks.build(NS, A_PRIVACY)),
            ],
        ]
    )


async def _render_root(event: CallbackQuery | Message, user: TelegramUser) -> None:
    await render(event, _summary(user), _root_keyboard(user))  # type: ignore[arg-type]


@router.message(F.text == texts.BTN_SETTINGS)
async def open_settings(message: Message, user: TelegramUser) -> None:
    await _render_root(message, user)


@router.callback_query(F.data.startswith(f"{NS}|{A_ROOT}"))
async def back_to_root(query: CallbackQuery, user: TelegramUser) -> None:
    await acknowledge(query)
    await _render_root(query, user)


@router.callback_query(F.data.startswith(f"{NS}|{A_NOTIF}"))
async def toggle_notifications(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    enabled = not user.notifications_enabled
    await container.users.set_notifications(session, user, enabled=enabled)
    await send_new(
        query,
        texts.SETTINGS_NOTIFICATIONS_ON if enabled else texts.SETTINGS_NOTIFICATIONS_OFF,
    )
    await _render_root(query, user)


@router.callback_query(F.data.startswith(f"{NS}|{A_QUIET}"))
async def ask_quiet_hours(query: CallbackQuery, state: FSMContext) -> None:
    await acknowledge(query)
    await state.set_state(SettingsStates.waiting_quiet_hours)
    await render(
        query,
        texts.SETTINGS_QUIET_ASK,
        build_actions([], back_target=callbacks.build(NS, A_ROOT), show_cancel=True),
    )


@router.message(SettingsStates.waiting_quiet_hours)
async def apply_quiet_hours(
    message: Message,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    state: FSMContext,
) -> None:
    raw = (message.text or "").strip().lower().replace(" ", "")
    if raw in {"off", "no", "disattiva"}:
        await container.users.set_quiet_hours(session, user, start=None, end=None)
        await state.clear()
        await message.answer(texts.SETTINGS_QUIET_OFF)
        await _render_root(message, user)
        return
    parts = raw.split("-")
    if len(parts) != 2:
        await message.answer(texts.FILTER_TIMES_INVALID, parse_mode="HTML")
        return
    try:
        start = time.fromisoformat(parts[0])
        end = time.fromisoformat(parts[1])
    except ValueError:
        await message.answer(texts.FILTER_TIMES_INVALID, parse_mode="HTML")
        return
    await container.users.set_quiet_hours(session, user, start=start, end=end)
    await state.clear()
    await message.answer(texts.SETTINGS_QUIET_SET)
    await _render_root(message, user)


@router.callback_query(F.data.startswith(f"{NS}|{A_PAGE}"))
async def ask_page_size(query: CallbackQuery, state: FSMContext) -> None:
    await acknowledge(query)
    await state.set_state(SettingsStates.waiting_page_size)
    await render(
        query,
        texts.SETTINGS_PAGE_SIZE_ASK,
        build_actions([], back_target=callbacks.build(NS, A_ROOT), show_cancel=True),
    )


@router.message(SettingsStates.waiting_page_size)
async def apply_page_size(
    message: Message,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    state: FSMContext,
) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        raise ValidationError("a number is required")
    await container.users.set_page_size(session, user, page_size=int(raw))
    await state.clear()
    await message.answer(texts.SETTINGS_SAVED)
    await _render_root(message, user)


@router.callback_query(F.data.startswith(f"{NS}|{A_SLOTS}"))
async def ask_max_slots(query: CallbackQuery, state: FSMContext) -> None:
    await acknowledge(query)
    await state.set_state(SettingsStates.waiting_max_slots)
    await render(
        query,
        texts.SETTINGS_MAX_SLOTS_ASK,
        build_actions([], back_target=callbacks.build(NS, A_ROOT), show_cancel=True),
    )


@router.message(SettingsStates.waiting_max_slots)
async def apply_max_slots(
    message: Message,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    state: FSMContext,
) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        raise ValidationError("a number is required")
    await container.users.set_max_slots(session, user, max_slots=int(raw))
    await state.clear()
    await message.answer(texts.SETTINGS_SAVED)
    await _render_root(message, user)


@router.callback_query(F.data.startswith(f"{NS}|{A_PRIVACY}"))
async def show_privacy(query: CallbackQuery) -> None:
    await acknowledge(query)
    await render(
        query,
        texts.PRIVACY_SHORT,
        build_actions(
            [
                [Entry("📤 Esporta impostazioni", callbacks.build(NS, A_EXPORT))],
                [Entry("🗑 Elimina tutti i miei dati", callbacks.build(NS, A_DELETE))],
            ],
            back_target=callbacks.build(NS, A_ROOT),
        ),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_EXPORT}"))
async def export_settings(query: CallbackQuery, container: Container, user: TelegramUser) -> None:
    """Export only non-sensitive settings; no health data is included."""
    await acknowledge(query)
    exported = container.users.export_settings(user)
    lines = [f"{key}: <b>{escape(str(value))}</b>" for key, value in exported.items()]
    await render(
        query,
        f"{texts.SETTINGS_EXPORT_TITLE}\n\n" + "\n".join(lines),
        build_actions([], back_target=callbacks.build(NS, A_PRIVACY)),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_DELETE_OK}"))
async def delete_data(
    query: CallbackQuery,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    await container.confirmations.consume(
        session,
        user=user,
        action=ConfirmationAction.DELETE_PROFILE,
        nonce=data.arg(0),
        context_parts=(user.id.hex,),
    )
    await container.users.delete_all_data(session, user)
    await render(query, texts.SETTINGS_DELETED)


@router.callback_query(F.data.startswith(f"{NS}|{A_DELETE}"))
async def confirm_delete(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    confirmation = await container.confirmations.issue(
        session,
        user=user,
        action=ConfirmationAction.DELETE_PROFILE,
        target_type="telegram_user",
        target_id=user.id.hex,
        context_parts=(user.id.hex,),
    )
    await render(
        query,
        texts.SETTINGS_DELETE_WARNING
        + "\n\n"
        + texts.SETTINGS_DELETE_CONFIRM.format(seconds=confirmation.expires_in_seconds),
        build_actions(
            [
                [
                    Entry(
                        "🗑 Elimina definitivamente",
                        callbacks.build(NS, A_DELETE_OK, confirmation.nonce),
                    )
                ]
            ],
            back_target=callbacks.build(NS, A_PRIVACY),
            show_cancel=True,
        ),
    )
