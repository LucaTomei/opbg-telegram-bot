"""Appointment screens: listing, details, PDF delivery and cancellation."""

from __future__ import annotations

import uuid

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import callbacks, texts
from app.bot.keyboards.inline import Entry, build_actions, build_list
from app.bot.presenters.formatting import format_appointment_card, format_local_datetime
from app.bot.routers.common import acknowledge, page_size, render, send_new
from app.composition import Container
from app.db.models.bookings import AppointmentCache
from app.db.models.users import TelegramUser
from app.db.repositories.accounts import PortalAccountRepository
from app.domain.enums import AppointmentStatus, ConfirmationAction
from app.domain.errors import NotFoundError, PdfUnavailable
from app.logging import get_logger

logger = get_logger(__name__)
router = Router(name="appointments")

NS = callbacks.APPOINTMENT
A_LIST = "list"
A_OPEN = "open"
A_SYNC = "sync"
A_PDF = "pdf"
A_CANCEL = "cancel"
A_CANCEL_OK = "cok"
A_HISTORY = "hist"


async def _render_list(
    event: CallbackQuery | Message,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    *,
    page: int,
    include_past: bool = False,
) -> None:
    result = await container.appointments.list_for_user(
        session, user, page=page, page_size=page_size(user), include_past=include_past
    )
    entries = [
        Entry(
            f"{format_local_datetime(item.scheduled_at_utc)} · "
            f"{(item.service_name or 'prestazione')[:24]}",
            callbacks.build(NS, A_OPEN, item.id),
        )
        for item in result.items
    ]
    header = [
        [
            _button(texts.BTN_REFRESH, callbacks.build(NS, A_SYNC, 0)),
            _button(
                "🗂 Storico" if not include_past else "📅 Prossimi",
                callbacks.build(NS, A_HISTORY, 0, "1" if not include_past else "0"),
            ),
        ]
    ]
    await render(
        event,
        texts.APPOINTMENTS_TITLE if result.total else texts.APPOINTMENTS_EMPTY,
        build_list(
            entries,
            namespace=NS,
            page_action=A_HISTORY if include_past else A_LIST,
            page=result.page,
            pages=result.pages,
            extra=("1",) if include_past else (),
            header_rows=header,
        ),
    )


def _button(label: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=label, callback_data=data)


@router.message(F.text == texts.BTN_APPOINTMENTS)
async def open_appointments(
    message: Message, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await _render_list(message, session, container, user, page=0)


@router.callback_query(F.data.startswith(f"{NS}|{A_LIST}"))
async def list_appointments(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    await _render_list(query, session, container, user, page=data.int_arg(0))


@router.callback_query(F.data.startswith(f"{NS}|{A_HISTORY}"))
async def list_history(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    await _render_list(
        query,
        session,
        container,
        user,
        page=data.int_arg(0),
        include_past=data.arg(1, "1") == "1",
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_SYNC}"))
async def sync_appointments(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    """Refresh every active account of the user from the portal."""
    await acknowledge(query)
    accounts = await PortalAccountRepository(session).list_for_user(user.id, include_disabled=False)
    created = updated = cancelled = 0
    for account in accounts:
        result = await container.appointments.sync_account(session, account, include_history=True)
        created += result.created
        updated += result.updated
        cancelled += result.cancelled
    await send_new(
        query,
        texts.APPOINTMENTS_SYNCED.format(created=created, updated=updated, cancelled=cancelled),
    )
    await _render_list(query, session, container, user, page=0)


def _appointment_actions(appointment: AppointmentCache) -> list[list[Entry]]:
    rows: list[list[Entry]] = []
    if appointment.pdf_available:
        rows.append([Entry(texts.BTN_DOWNLOAD_PDF, callbacks.build(NS, A_PDF, appointment.id))])
    if appointment.status is AppointmentStatus.PENDING:
        rows.append(
            [Entry(texts.BTN_CANCEL_APPOINTMENT, callbacks.build(NS, A_CANCEL, appointment.id))]
        )
    return rows


async def _render_appointment(
    event: CallbackQuery | Message,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    appointment_id: uuid.UUID,
    *,
    notice: str = "",
) -> None:
    appointment = await container.appointments.require_appointment(session, user, appointment_id)
    text = format_appointment_card(appointment)
    if notice:
        text = f"{text}\n\n{notice}"
    await render(
        event,
        text,
        build_actions(
            _appointment_actions(appointment),
            back_target=callbacks.build(NS, A_LIST, 0),
        ),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_OPEN}"))
async def open_appointment(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    await _render_appointment(query, session, container, user, data.uuid_arg(0))


@router.callback_query(F.data.startswith(f"{NS}|{A_PDF}"))
async def deliver_pdf(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    """Deliver the reminder as a Telegram document or as a one-time link."""
    await acknowledge(query)
    data = callbacks.parse(query.data)
    appointment = await container.appointments.require_appointment(session, user, data.uuid_arg(0))
    try:
        document = await container.appointments.download_pdf(session, appointment=appointment)
    except PdfUnavailable:
        await _render_appointment(
            query,
            session,
            container,
            user,
            appointment.id,
            notice=texts.APPOINTMENT_PDF_UNAVAILABLE,
        )
        return
    if isinstance(query.message, Message):
        await query.message.answer_document(
            BufferedInputFile(document.content, filename=document.filename),
            caption=texts.APPOINTMENT_PDF_SENT,
        )
    # The buffer is dropped here; nothing is written to disk.


@router.callback_query(F.data.startswith(f"{NS}|{A_CANCEL_OK}"))
async def cancel_confirmed(
    query: CallbackQuery,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    correlation_id: str,
) -> None:
    """Consume the nonce and send the cancellation exactly once."""
    await acknowledge(query)
    data = callbacks.parse(query.data)
    appointment = await container.appointments.require_appointment(session, user, data.uuid_arg(0))
    await container.confirmations.consume(
        session,
        user=user,
        action=ConfirmationAction.CANCEL_APPOINTMENT,
        nonce=data.arg(1),
        context_parts=(appointment.id.hex, appointment.portal_external_id),
    )
    result = await container.appointments.cancel(session, user=user, appointment=appointment)
    if result.succeeded:
        await _render_appointment(
            query, session, container, user, appointment.id, notice=texts.APPOINTMENT_CANCELLED
        )
        return
    if result.ambiguous:
        await render(query, texts.APPOINTMENT_CANCEL_AMBIGUOUS.format(cid=correlation_id))
        await _render_list(query, session, container, user, page=0)
        return
    await _render_appointment(query, session, container, user, appointment.id, notice=result.detail)


@router.callback_query(F.data.startswith(f"{NS}|{A_CANCEL}"))
async def confirm_cancel(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    """First confirmation step for a cancellation."""
    await acknowledge(query)
    data = callbacks.parse(query.data)
    appointment = await container.appointments.require_appointment(session, user, data.uuid_arg(0))
    if appointment.status is not AppointmentStatus.PENDING:
        raise NotFoundError("the appointment is no longer active")
    confirmation = await container.confirmations.issue(
        session,
        user=user,
        action=ConfirmationAction.CANCEL_APPOINTMENT,
        target_type="appointment",
        target_id=appointment.id.hex,
        context_parts=(appointment.id.hex, appointment.portal_external_id),
    )
    await render(
        query,
        texts.APPOINTMENT_CANCEL_WARNING.format(details=format_appointment_card(appointment))
        + "\n\n"
        + texts.APPOINTMENT_CANCEL_CONFIRM.format(seconds=confirmation.expires_in_seconds),
        build_actions(
            [
                [
                    Entry(
                        "🗑 Conferma disdetta",
                        callbacks.build(NS, A_CANCEL_OK, appointment.id, confirmation.nonce),
                    )
                ]
            ],
            back_target=callbacks.build(NS, A_OPEN, appointment.id),
            show_cancel=True,
        ),
    )
