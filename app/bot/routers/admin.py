"""Administration inside Telegram.

Lists always show masked data.  Revealing personal data and every mutating
action require an explicit reason and produce an immutable audit entry; actions
performed on behalf of a user additionally require a double confirmation.
Secrets are never displayed.
"""

from __future__ import annotations

import uuid
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import callbacks, texts
from app.bot.keyboards.inline import Entry, build_actions, build_list
from app.bot.presenters.formatting import (
    escape,
    format_local_datetime,
    format_watch_row,
)
from app.bot.routers.common import acknowledge, page_size, render, send_new
from app.composition import Container
from app.db.models.users import TelegramUser
from app.db.repositories.accounts import PatientRepository
from app.db.repositories.base import Page
from app.db.repositories.bookings import AppointmentRepository
from app.db.repositories.watches import WatchRepository
from app.domain.enums import (
    AuditAction,
    BreakerScope,
    BreakerState,
    ConfirmationAction,
    PortalAccountStatus,
    UserStatus,
    WatchStatus,
)
from app.domain.errors import NotFoundError, ValidationError
from app.logging import get_logger
from app.security.redaction import mask_person_name

logger = get_logger(__name__)
router = Router(name="admin")

NS = callbacks.ADMIN
A_ROOT = "root"
A_USERS = "usr"
A_USER = "u1"
A_INVITES = "inv"
A_INVITE_NEW = "invn"
A_INVITE_REVOKE = "invr"
A_ACCOUNTS = "acc"
A_ACCOUNT = "a1"
A_PATIENTS = "pat"
A_WATCHES = "wch"
A_WATCH = "w1"
A_ATTEMPTS = "att"
A_APPOINTMENTS = "app"
A_APPOINTMENT = "ap1"
A_NOTIFICATIONS = "ntf"
A_EVENTS = "evt"
A_EVENT_RESOLVE = "evr"
A_JOBS = "job"
A_AUDIT = "aud"
A_SYSTEM = "sys"
A_BREAKERS = "brk"
A_BREAKER_SET = "brs"
A_REVEAL = "rev"
A_SET_STATUS = "ss"
A_ACC_STATUS = "as"
A_WATCH_ACTION = "wa"
A_DELEGATED = "dlg"
A_CONFIRM = "cf"
A_SEEN_SLOTS = "seen"
A_RUN_LOGS = "runs"


class AdminStates(StatesGroup):
    """Free-text steps of the administrative screens."""

    waiting_reason = State()
    waiting_invite_uses = State()


SECTIONS: tuple[tuple[str, str], ...] = (
    ("👥 Utenti", A_USERS),
    ("🎟 Inviti", A_INVITES),
    ("🔐 Account portale", A_ACCOUNTS),
    ("🧒 Pazienti", A_PATIENTS),
    ("🔔 Monitoraggi", A_WATCHES),
    ("📝 Tentativi prenotazione", A_ATTEMPTS),
    ("📅 Appuntamenti cache", A_APPOINTMENTS),
    ("✉️ Notifiche", A_NOTIFICATIONS),
    ("⚠️ Errori e circuit breaker", A_EVENTS),
    ("⚙️ Code e lease", A_JOBS),
    ("🧾 Audit", A_AUDIT),
    ("📊 Stato sistema", A_SYSTEM),
)


def _root_keyboard() -> InlineKeyboardMarkup:
    rows = [[Entry(label, callbacks.build(NS, action, 0))] for label, action in SECTIONS]
    return build_actions(rows)


@router.message(F.text == texts.BTN_ADMIN)
async def open_admin(message: Message) -> None:
    await message.answer(texts.ADMIN_TITLE, reply_markup=_root_keyboard(), parse_mode="HTML")


@router.message(F.text.startswith("/admin"))
async def admin_command(message: Message) -> None:
    await message.answer(texts.ADMIN_TITLE, reply_markup=_root_keyboard(), parse_mode="HTML")


@router.callback_query(F.data.startswith(f"{NS}|{A_ROOT}"))
async def admin_root(query: CallbackQuery) -> None:
    await acknowledge(query)
    await render(query, texts.ADMIN_TITLE, _root_keyboard())


def _page_of(result: Page[Any]) -> tuple[int, int]:
    return result.page, result.pages


# --- users -------------------------------------------------------------------
@router.callback_query(F.data.startswith(f"{NS}|{A_USERS}"))
async def list_users(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    result = await container.admin.list_users(
        session, user, page=data.int_arg(0), page_size=page_size(user)
    )
    entries = [
        Entry(
            f"{container.admin.mask_user(item).display_name} · {item.status.value.lower()}",
            callbacks.build(NS, A_USER, item.id),
        )
        for item in result.items
    ]
    page, pages = _page_of(result)
    await render(
        query,
        f"<b>Utenti</b> · totale {result.total}" if result.total else texts.EMPTY_PAGE,
        build_list(
            entries,
            namespace=NS,
            page_action=A_USERS,
            page=page,
            pages=pages,
            back_target=callbacks.build(NS, A_ROOT),
        ),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_USER}"))
async def open_user(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    target_id = data.uuid_arg(0)
    result = await container.admin.list_users(session, user, page=0, page_size=1000)
    target = next((item for item in result.items if item.id == target_id), None)
    if target is None:
        raise NotFoundError("user not found")
    masked = container.admin.mask_user(target)
    lines = [
        f"<b>{escape(masked.display_name)}</b>",
        f"Username: {escape(masked.username)}",
        f"Stato: <b>{target.status.value.lower()}</b>",
        f"Ruolo: <b>{target.role.value.lower()}</b>",
        f"Informativa: <b>{target.consent_version or 'non accettata'}</b>",
        f"Ultimo accesso: {format_local_datetime(target.last_seen_at)}",
        "",
        texts.ADMIN_NO_SECRETS,
    ]
    rows = [
        [
            Entry("🔓 Mostra dati completi", callbacks.build(NS, A_REVEAL, "u", target.id)),
        ],
        [
            Entry("⏸ Sospendi", callbacks.build(NS, A_SET_STATUS, target.id, "S")),
            Entry("▶️ Riattiva", callbacks.build(NS, A_SET_STATUS, target.id, "A")),
        ],
        [Entry("⛔️ Blocca", callbacks.build(NS, A_SET_STATUS, target.id, "B"))],
        [
            Entry(
                "🔐 Account dell'utente",
                callbacks.build(NS, A_ACCOUNTS, 0, target.id),
            ),
            Entry("🔔 Monitoraggi", callbacks.build(NS, A_WATCHES, 0, target.id)),
        ],
    ]
    await render(
        query,
        "\n".join(lines),
        build_actions(rows, back_target=callbacks.build(NS, A_USERS, 0)),
    )


_STATUS_CODES: dict[str, UserStatus] = {
    "S": UserStatus.SUSPENDED,
    "A": UserStatus.ACTIVE,
    "B": UserStatus.BLOCKED,
}


# --- reason capture ----------------------------------------------------------
async def _ask_reason(
    query: CallbackQuery,
    state: FSMContext,
    *,
    action: str,
    payload: dict[str, str],
    back_target: str,
) -> None:
    """Every administrative mutation starts by asking for a written reason."""
    await state.set_state(AdminStates.waiting_reason)
    await state.update_data(admin_action=action, admin_payload=payload)
    await render(
        query,
        texts.ADMIN_ASK_REASON,
        build_actions([], back_target=back_target, show_cancel=True),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_SET_STATUS}"))
async def ask_user_status_reason(query: CallbackQuery, state: FSMContext) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    await _ask_reason(
        query,
        state,
        action="user_status",
        payload={"user_id": data.uuid_arg(0).hex, "status": data.arg(1)},
        back_target=callbacks.build(NS, A_USER, data.uuid_arg(0)),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_REVEAL}"))
async def ask_reveal_reason(query: CallbackQuery, state: FSMContext) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    kind = data.arg(0)
    target = data.uuid_arg(1)
    back = {
        "u": callbacks.build(NS, A_USER, target),
        "a": callbacks.build(NS, A_ACCOUNT, target),
        "p": callbacks.build(NS, A_PATIENTS, 0),
    }.get(kind, callbacks.build(NS, A_ROOT))
    await _ask_reason(
        query,
        state,
        action=f"reveal_{kind}",
        payload={"target_id": target.hex},
        back_target=back,
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_ACC_STATUS}"))
async def ask_account_status_reason(query: CallbackQuery, state: FSMContext) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    await _ask_reason(
        query,
        state,
        action="account_status",
        payload={"account_id": data.uuid_arg(0).hex, "status": data.arg(1)},
        back_target=callbacks.build(NS, A_ACCOUNT, data.uuid_arg(0)),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_WATCH_ACTION}"))
async def ask_watch_action_reason(query: CallbackQuery, state: FSMContext) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    await _ask_reason(
        query,
        state,
        action="watch_action",
        payload={"watch_id": data.uuid_arg(0).hex, "op": data.arg(1)},
        back_target=callbacks.build(NS, A_WATCH, data.uuid_arg(0)),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_DELEGATED}"))
async def ask_delegated_reason(query: CallbackQuery, state: FSMContext) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    await _ask_reason(
        query,
        state,
        action=f"delegated_{data.arg(0)}",
        payload={"target_id": data.uuid_arg(1).hex},
        back_target=callbacks.build(NS, A_ROOT),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_BREAKER_SET}"))
async def ask_breaker_reason(query: CallbackQuery, state: FSMContext) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    await _ask_reason(
        query,
        state,
        action="breaker",
        payload={"scope": data.arg(0), "subject": data.arg(1), "state": data.arg(2)},
        back_target=callbacks.build(NS, A_BREAKERS, 0),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_EVENT_RESOLVE}"))
async def ask_event_reason(query: CallbackQuery, state: FSMContext) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    await _ask_reason(
        query,
        state,
        action="resolve_event",
        payload={"event_id": data.uuid_arg(0).hex},
        back_target=callbacks.build(NS, A_EVENTS, 0),
    )


@router.message(AdminStates.waiting_reason)
async def receive_reason(
    message: Message,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    state: FSMContext,
) -> None:
    """Validate the reason, then ask for the second confirmation."""
    stored = await state.get_data()
    action = str(stored.get("admin_action", ""))
    payload = stored.get("admin_payload") or {}
    try:
        reason = container.confirmations.validate_reason(message.text or "")
    except ValidationError:
        await message.answer(texts.ADMIN_REASON_TOO_SHORT)
        return
    await state.update_data(admin_reason=reason)
    await state.set_state(state=None)
    confirmation = await container.confirmations.issue(
        session,
        user=user,
        action=_confirmation_action(action),
        target_type="admin_action",
        target_id=str(payload.get("target_id") or payload.get("user_id") or action)[:64],
        context_parts=(action, *[f"{key}={value}" for key, value in sorted(payload.items())]),
        reason=reason,
    )
    await message.answer(
        texts.ADMIN_DELEGATED_WARNING
        if action.startswith("delegated")
        else "Confermi l'operazione? Verrà registrata nell'audit.",
        reply_markup=build_actions(
            [
                [
                    Entry(
                        "✅ Conferma",
                        callbacks.build(NS, A_CONFIRM, confirmation.nonce),
                    )
                ]
            ],
            back_target=callbacks.build(NS, A_ROOT),
            show_cancel=True,
        ),
        parse_mode="HTML",
    )


def _confirmation_action(action: str) -> ConfirmationAction:
    mapping = {
        "user_status": ConfirmationAction.ADMIN_BLOCK_USER,
        "account_status": ConfirmationAction.ADMIN_BLOCK_USER,
        "watch_action": ConfirmationAction.ADMIN_BLOCK_USER,
        "breaker": ConfirmationAction.ADMIN_BREAKER_OVERRIDE,
        "resolve_event": ConfirmationAction.ADMIN_BLOCK_USER,
        "delegated_cancel": ConfirmationAction.ADMIN_DELEGATED_CANCEL,
        "delegated_book": ConfirmationAction.ADMIN_DELEGATED_BOOK,
        "delegated_pdf": ConfirmationAction.ADMIN_DELEGATED_CANCEL,
    }
    if action.startswith("reveal_"):
        return ConfirmationAction.ADMIN_REVEAL_PII
    return mapping.get(action, ConfirmationAction.ADMIN_BLOCK_USER)


@router.callback_query(F.data.startswith(f"{NS}|{A_CONFIRM}"))
async def apply_admin_action(
    query: CallbackQuery,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    state: FSMContext,
    correlation_id: str,
) -> None:
    """Consume the confirmation and execute the requested administrative action."""
    await acknowledge(query)
    data = callbacks.parse(query.data)
    stored = await state.get_data()
    action = str(stored.get("admin_action", ""))
    payload: dict[str, str] = dict(stored.get("admin_payload") or {})
    reason = str(stored.get("admin_reason", ""))
    if not action or not reason:
        raise NotFoundError("no administrative action is pending")
    await container.confirmations.consume(
        session,
        user=user,
        action=_confirmation_action(action),
        nonce=data.arg(0),
        context_parts=(action, *[f"{key}={value}" for key, value in sorted(payload.items())]),
    )
    await state.clear()
    text = await _execute_admin_action(
        session, container, user, action, payload, reason, correlation_id
    )
    await render(query, text, build_actions([], back_target=callbacks.build(NS, A_ROOT)))


async def _execute_admin_action(
    session: AsyncSession,
    container: Container,
    admin: TelegramUser,
    action: str,
    payload: dict[str, str],
    reason: str,
    correlation_id: str,
) -> str:
    if action == "user_status":
        status = _STATUS_CODES.get(payload.get("status", ""), UserStatus.SUSPENDED)
        await container.admin.set_user_status(
            session,
            admin,
            user_id=uuid.UUID(hex=payload["user_id"]),
            status=status,
            reason=reason,
            correlation_id=correlation_id,
        )
        return f"{texts.ADMIN_ACTION_DONE}\nNuovo stato: <b>{status.value.lower()}</b>"
    if action == "account_status":
        account_status_map = {
            "A": PortalAccountStatus.ACTIVE,
            "D": PortalAccountStatus.DISABLED,
            "L": PortalAccountStatus.LOCKED,
        }
        account_status = account_status_map.get(
            payload.get("status", ""), PortalAccountStatus.DISABLED
        )
        await container.admin.set_account_status(
            session,
            admin,
            account_id=uuid.UUID(hex=payload["account_id"]),
            status=account_status,
            reason=reason,
            correlation_id=correlation_id,
        )
        return (
            f"{texts.ADMIN_ACTION_DONE}\nNuovo stato account: <b>{account_status.value.lower()}</b>"
        )
    if action == "watch_action":
        watch_id = uuid.UUID(hex=payload["watch_id"])
        operation = payload.get("op", "")
        if operation == "run":
            await container.admin.run_watch_now(
                session, admin, watch_id=watch_id, reason=reason, correlation_id=correlation_id
            )
            return texts.ADMIN_ACTION_DONE
        if operation in {"pause", "resume"}:
            await container.admin.set_watch_status(
                session,
                admin,
                watch_id=watch_id,
                status=WatchStatus.PAUSED if operation == "pause" else WatchStatus.ACTIVE,
                reason=reason,
                correlation_id=correlation_id,
            )
            return texts.ADMIN_ACTION_DONE
        if operation in {"non", "noff"}:
            await container.admin.set_watch_notifications(
                session,
                admin,
                watch_id=watch_id,
                enabled=operation == "non",
                reason=reason,
                correlation_id=correlation_id,
            )
            return texts.ADMIN_ACTION_DONE
        raise ValidationError("unknown monitor operation")
    if action == "breaker":
        await container.admin.override_breaker(
            session,
            admin,
            scope=BreakerScope(payload.get("scope", BreakerScope.GLOBAL.value)),
            subject=payload.get("subject", "GLOBAL"),
            state=BreakerState(payload.get("state", BreakerState.CLOSED.value)),
            reason=reason,
            correlation_id=correlation_id,
        )
        return texts.ADMIN_ACTION_DONE
    if action == "resolve_event":
        await container.admin.resolve_event(
            session,
            admin,
            event_id=uuid.UUID(hex=payload["event_id"]),
            note=reason,
            correlation_id=correlation_id,
        )
        return texts.ADMIN_ACTION_DONE
    if action.startswith("reveal_"):
        kind = action.removeprefix("reveal_")
        target = uuid.UUID(hex=payload["target_id"])
        if kind == "u":
            revealed = await container.admin.reveal_user(
                session, admin, user_id=target, reason=reason, correlation_id=correlation_id
            )
        elif kind == "a":
            revealed = await container.admin.reveal_account(
                session, admin, account_id=target, reason=reason, correlation_id=correlation_id
            )
        else:
            revealed = await container.admin.reveal_patient(
                session, admin, patient_id=target, reason=reason, correlation_id=correlation_id
            )
        lines = [f"{key}: <b>{escape(str(value))}</b>" for key, value in revealed.items()]
        return f"{texts.ADMIN_REVEAL_NOTICE}\n\n" + "\n".join(lines)
    if action == "delegated_cancel":
        return await _delegated_cancel(
            session, container, admin, uuid.UUID(hex=payload["target_id"]), reason, correlation_id
        )
    if action == "delegated_book":
        return await _delegated_book(
            session, container, admin, uuid.UUID(hex=payload["target_id"]), reason, correlation_id
        )
    if action == "delegated_pdf":
        return await _delegated_pdf(
            session, container, admin, uuid.UUID(hex=payload["target_id"]), reason, correlation_id
        )
    raise ValidationError("unknown administrative action")


async def _delegated_pdf(
    session: AsyncSession,
    container: Container,
    admin: TelegramUser,
    appointment_id: uuid.UUID,
    reason: str,
    correlation_id: str,
) -> str:
    """Issue a one-time reminder link for another user, with a full audit entry."""
    from app.db.repositories.users import UserRepository
    from app.domain.enums import NotificationEventType
    from app.domain.errors import PdfUnavailable

    appointments = AppointmentRepository(session)
    appointment = await appointments.get_any(appointment_id)
    if appointment is None:
        raise NotFoundError("appointment not found")
    owner = await UserRepository(session).get(appointment.telegram_user_id)
    if owner is None:
        raise NotFoundError("appointment owner not found")

    await container.admin.record_delegated_action(
        session,
        admin,
        action=AuditAction.DELEGATED_PDF_DOWNLOAD,
        target_type="appointment",
        target_id=appointment.id.hex,
        reason=reason,
        correlation_id=correlation_id,
    )
    try:
        await container.appointments.download_pdf(session, appointment=appointment)
    except PdfUnavailable:
        await container.notifications.queue(
            session,
            user=owner,
            event_type=NotificationEventType.PDF_UNAVAILABLE,
            subject=f"{appointment.id.hex}:pdf",
            payload={"appointment_id": appointment.id.hex},
            appointment_id=appointment.id,
            correlation_id=correlation_id,
        )
        return f"{texts.ADMIN_ACTION_DONE}\n{texts.APPOINTMENT_PDF_UNAVAILABLE}"
    url = await container.appointments.issue_download_link(
        session, user=owner, appointment=appointment, issued_by_admin_id=admin.id
    )
    return (
        f"{texts.ADMIN_ACTION_DONE}\n"
        f"Link monouso valido {container.settings.pdf_link_ttl_minutes} minuti:\n{url}"
    )


async def _delegated_cancel(
    session: AsyncSession,
    container: Container,
    admin: TelegramUser,
    appointment_id: uuid.UUID,
    reason: str,
    correlation_id: str,
) -> str:
    """Cancel an appointment on behalf of its owner, with a full audit entry."""
    appointments = AppointmentRepository(session)
    appointment = await appointments.get_any(appointment_id)
    if appointment is None:
        raise NotFoundError("appointment not found")
    from app.db.repositories.users import UserRepository

    owner = await UserRepository(session).get(appointment.telegram_user_id)
    if owner is None:
        raise NotFoundError("appointment owner not found")
    await container.admin.record_delegated_action(
        session,
        admin,
        action=AuditAction.DELEGATED_CANCELLATION,
        target_type="appointment",
        target_id=appointment.id.hex,
        reason=reason,
        correlation_id=correlation_id,
    )
    result = await container.appointments.cancel(
        session, user=owner, appointment=appointment, initiated_by_admin_id=admin.id
    )
    from app.domain.enums import NotificationEventType

    await container.notifications.queue(
        session,
        user=owner,
        event_type=(
            NotificationEventType.CANCELLATION_SUCCESS
            if result.succeeded
            else NotificationEventType.CANCELLATION_FAILED
        ),
        subject=f"{appointment.id.hex}:cancel",
        payload={"detail": result.detail, "by_admin": True},
        appointment_id=appointment.id,
        correlation_id=correlation_id,
    )
    if result.succeeded:
        return f"{texts.ADMIN_ACTION_DONE}\n{texts.APPOINTMENT_CANCELLED}"
    if result.ambiguous:
        return texts.APPOINTMENT_CANCEL_AMBIGUOUS.format(cid=correlation_id)
    return f"{texts.ADMIN_ACTION_DONE}\n{escape(result.detail)}"


async def _delegated_book(
    session: AsyncSession,
    container: Container,
    admin: TelegramUser,
    watch_id: uuid.UUID,
    reason: str,
    correlation_id: str,
) -> str:
    """Book the best slot of a monitor on behalf of its owner."""
    from app.db.repositories.accounts import PortalAccountRepository
    from app.db.repositories.base import utc_now
    from app.db.repositories.users import UserRepository
    from app.services.bookings import BookingRequest
    from app.services.mapping import watch_filter, watch_selection

    watches = WatchRepository(session)
    watch = await watches.get_any(watch_id)
    if watch is None:
        raise NotFoundError("monitor not found")
    owner = await UserRepository(session).get(watch.telegram_user_id)
    account = await PortalAccountRepository(session).get(watch.portal_account_id)
    patient = await PatientRepository(session).get(watch.patient_id)
    if owner is None or account is None or patient is None:
        raise NotFoundError("the monitor references missing data")

    await container.admin.record_delegated_action(
        session,
        admin,
        action=AuditAction.DELEGATED_BOOKING,
        target_type="booking_watch",
        target_id=watch.id.hex,
        reason=reason,
        correlation_id=correlation_id,
    )
    locations = await watches.locations_for(watch.id)
    slot_filter = watch_filter(watch, locations)
    selection = watch_selection(watch)
    now = utc_now()
    result = await container.search.collect(
        session, account, patient, selection, slot_filter, now=now
    )
    if not result.slots:
        return f"{texts.ADMIN_ACTION_DONE}\nNessuno slot compatibile disponibile."
    booking = await container.bookings.book(
        session,
        BookingRequest(
            account=account,
            patient=patient,
            selection=selection,
            slot_filter=slot_filter,
            slot=result.slots[0],
            user_id=owner.id,
            watch_id=watch.id,
            initiated_by_admin_id=admin.id,
        ),
        now=now,
    )
    return f"{texts.ADMIN_ACTION_DONE}\n{escape(booking.outcome.detail)}"


# --- invites -----------------------------------------------------------------
@router.callback_query(F.data.startswith(f"{NS}|{A_INVITE_NEW}"))
async def ask_invite_uses(query: CallbackQuery, state: FSMContext) -> None:
    await acknowledge(query)
    await state.set_state(AdminStates.waiting_invite_uses)
    await render(
        query,
        texts.ADMIN_INVITE_ASK_USES,
        build_actions([], back_target=callbacks.build(NS, A_INVITES, 0), show_cancel=True),
    )


@router.message(AdminStates.waiting_invite_uses)
async def create_invite(
    message: Message,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    state: FSMContext,
) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        raise ValidationError("a number is required")
    code, _ = await container.invites.create(session, admin=user, max_uses=int(raw))
    await state.clear()
    await message.answer(
        texts.ADMIN_INVITE_CREATED.format(
            code=code,
            uses=int(raw),
            expires=f"{container.settings.invite_ttl_hours} ore",
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_INVITE_REVOKE}"))
async def revoke_invite(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    await container.invites.revoke(session, admin=user, invite_id=data.uuid_arg(0))
    await send_new(query, texts.ADMIN_INVITE_REVOKED)
    await _render_invites(query, session, container, user, page=0)


async def _render_invites(
    event: CallbackQuery | Message,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    *,
    page: int,
) -> None:
    result = await container.admin.list_invites(session, user, page=page, page_size=page_size(user))
    entries = [
        Entry(
            f"{'revocato' if invite.revoked_at else 'attivo'} · "
            f"usi {invite.used_count}/{invite.max_uses} · "
            f"scade {format_local_datetime(invite.expires_at)}",
            callbacks.build(NS, A_INVITE_REVOKE, invite.id),
        )
        for invite in result.items
    ]
    await render(
        event,
        "<b>Inviti</b>\nTocca una voce per revocarla.",
        build_list(
            entries,
            namespace=NS,
            page_action=A_INVITES,
            page=result.page,
            pages=result.pages,
            back_target=callbacks.build(NS, A_ROOT),
            header_rows=[[_btn("➕ Nuovo invito", callbacks.build(NS, A_INVITE_NEW))]],
        ),
    )


def _btn(label: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=label, callback_data=data)


@router.callback_query(F.data.startswith(f"{NS}|{A_INVITES}"))
async def list_invites(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    await _render_invites(query, session, container, user, page=data.int_arg(0))


# --- portal accounts ---------------------------------------------------------
@router.callback_query(F.data.startswith(f"{NS}|{A_ACCOUNTS}"))
async def list_accounts(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    owner = data.arg(1)
    owner_id = uuid.UUID(bytes=callbacks.unpack_uuid(owner).bytes) if owner else None
    result = await container.admin.list_accounts(
        session, user, page=data.int_arg(0), page_size=page_size(user), user_id=owner_id
    )
    entries = [
        Entry(
            f"{item.label} · {item.status.value.lower()} · {container.admin.mask_account(item)}",
            callbacks.build(NS, A_ACCOUNT, item.id),
        )
        for item in result.items
    ]
    await render(
        query,
        f"<b>Account portale</b> · totale {result.total}" if result.total else texts.EMPTY_PAGE,
        build_list(
            entries,
            namespace=NS,
            page_action=A_ACCOUNTS,
            page=result.page,
            pages=result.pages,
            extra=(owner,) if owner else (),
            back_target=callbacks.build(NS, A_ROOT),
        ),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_ACCOUNT}"))
async def open_account(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    account_id = data.uuid_arg(0)
    result = await container.admin.list_accounts(session, user, page=0, page_size=1000)
    account = next((item for item in result.items if item.id == account_id), None)
    if account is None:
        raise NotFoundError("portal account not found")
    lines = [
        f"<b>{escape(account.label)}</b>",
        f"Email: {escape(container.admin.mask_account(account))}",
        f"Stato: <b>{account.status.value.lower()}</b>",
        f"Ultimo accesso: {format_local_datetime(account.last_login_at)}",
        f"Ultima sincronizzazione: {format_local_datetime(account.last_sync_at)}",
        f"Errori di autenticazione consecutivi: <b>{account.consecutive_auth_failures}</b>",
        f"Ultimo errore: <code>{escape(account.last_error_code or '-')}</code>",
        "",
        texts.ADMIN_NO_SECRETS,
    ]
    rows = [
        [Entry("🔓 Mostra email completa", callbacks.build(NS, A_REVEAL, "a", account.id))],
        [
            Entry("⏸ Disabilita", callbacks.build(NS, A_ACC_STATUS, account.id, "D")),
            Entry("▶️ Riabilita", callbacks.build(NS, A_ACC_STATUS, account.id, "A")),
        ],
        [Entry("🧒 Pazienti", callbacks.build(NS, A_PATIENTS, 0, account.id))],
    ]
    await render(
        query,
        "\n".join(lines),
        build_actions(rows, back_target=callbacks.build(NS, A_ACCOUNTS, 0)),
    )


# --- patients ----------------------------------------------------------------
@router.callback_query(F.data.startswith(f"{NS}|{A_PATIENTS}"))
async def list_patients(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    """Patients are always listed masked; the tax code requires a reveal."""
    await acknowledge(query)
    data = callbacks.parse(query.data)
    account_arg = data.arg(1)
    account_id = callbacks.unpack_uuid(account_arg) if account_arg else None
    result = await container.admin.list_patients(
        session, user, page=data.int_arg(0), page_size=page_size(user), account_id=account_id
    )
    entries = [
        Entry(
            f"{container.admin.masked_patient(item)['display_name']} · "
            f"{container.admin.masked_patient(item)['tax_code']}",
            callbacks.build(NS, A_REVEAL, "p", item.id),
        )
        for item in result.items
    ]
    await render(
        query,
        f"<b>Pazienti</b> · totale {result.total}" if result.total else texts.EMPTY_PAGE,
        build_list(
            entries,
            namespace=NS,
            page_action=A_PATIENTS,
            page=result.page,
            pages=result.pages,
            extra=(account_arg,) if account_arg else (),
            back_target=callbacks.build(NS, A_ROOT),
        ),
    )


# --- watches -----------------------------------------------------------------
@router.callback_query(F.data.startswith(f"{NS}|{A_WATCHES}"))
async def list_watches(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    owner = data.arg(1)
    owner_id = callbacks.unpack_uuid(owner) if owner else None
    result = await container.admin.list_watches(
        session, user, page=data.int_arg(0), page_size=page_size(user), user_id=owner_id
    )
    entries = [
        Entry(format_watch_row(item), callbacks.build(NS, A_WATCH, item.id))
        for item in result.items
    ]
    await render(
        query,
        f"<b>Monitoraggi</b> · totale {result.total}" if result.total else texts.EMPTY_PAGE,
        build_list(
            entries,
            namespace=NS,
            page_action=A_WATCHES,
            page=result.page,
            pages=result.pages,
            extra=(owner,) if owner else (),
            back_target=callbacks.build(NS, A_ROOT),
        ),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_WATCH}"))
async def open_watch(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    watch = await WatchRepository(session).get_any(data.uuid_arg(0))
    if watch is None:
        raise NotFoundError("monitor not found")
    patient = await PatientRepository(session).get(watch.patient_id)
    patient_label = mask_person_name(patient.display_name or "") if patient is not None else "-"
    lines = [
        f"<b>{escape(watch.name)}</b>",
        f"Stato: <b>{watch.status.value.lower()}</b> · Modalità: <b>{watch.mode.value.lower()}</b>",
        f"Notifiche: <b>{'attive' if watch.notifications_enabled else 'disattivate'}</b>",
        f"Paziente: {escape(patient_label)}",
        f"Ultimo controllo: {format_local_datetime(watch.last_checked_at)}",
        f"Prossimo controllo: {format_local_datetime(watch.next_check_at)}",
        f"Errori consecutivi: <b>{watch.consecutive_errors}</b>",
        f"Ultimo errore: <code>{escape(watch.last_error_code or '-')}</code>",
        f"Lease: <code>{escape(watch.lease_owner or '-')}</code>",
    ]
    rows = [
        [
            Entry("⏸ Pausa", callbacks.build(NS, A_WATCH_ACTION, watch.id, "pause")),
            Entry("▶️ Riprendi", callbacks.build(NS, A_WATCH_ACTION, watch.id, "resume")),
        ],
        [
            Entry("▶️ Esegui ora", callbacks.build(NS, A_WATCH_ACTION, watch.id, "run")),
            Entry("🔕 Notifiche OFF", callbacks.build(NS, A_WATCH_ACTION, watch.id, "noff")),
        ],
        [
            Entry("👁 Slot visti", callbacks.build(NS, A_SEEN_SLOTS, 0, watch.id)),
            Entry("📜 Esecuzioni", callbacks.build(NS, A_RUN_LOGS, 0, watch.id)),
        ],
        [
            Entry(
                "🤝 Prenota per conto dell'utente",
                callbacks.build(NS, A_DELEGATED, "book", watch.id),
            )
        ],
    ]
    await render(
        query,
        "\n".join(lines),
        build_actions(rows, back_target=callbacks.build(NS, A_WATCHES, 0)),
    )


# --- booking attempts --------------------------------------------------------
@router.callback_query(F.data.startswith(f"{NS}|{A_ATTEMPTS}"))
async def list_attempts(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    result = await container.admin.list_attempts(
        session, user, page=data.int_arg(0), page_size=page_size(user)
    )
    lines = [
        f"• <code>{escape(item.status.value)}</code> · {format_local_datetime(item.started_at)} "
        f"· {escape(item.location_name or '-')} "
        f"· <code>{escape(item.error_code or '-')}</code>"
        for item in result.items
    ]
    await render(
        query,
        f"<b>Tentativi di prenotazione</b> · totale {result.total}\n\n"
        + ("\n".join(lines) if lines else texts.EMPTY_PAGE),
        build_list(
            [],
            namespace=NS,
            page_action=A_ATTEMPTS,
            page=result.page,
            pages=result.pages,
            back_target=callbacks.build(NS, A_ROOT),
        ),
    )


# --- cached appointments -----------------------------------------------------
@router.callback_query(F.data.startswith(f"{NS}|{A_APPOINTMENTS}"))
async def list_appointments(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    result = await container.admin.list_appointments(
        session, user, page=data.int_arg(0), page_size=page_size(user)
    )
    entries = [
        Entry(
            f"{format_local_datetime(item.scheduled_at_utc)} · {item.status.value.lower()}",
            callbacks.build(NS, A_APPOINTMENT, item.id),
        )
        for item in result.items
    ]
    await render(
        query,
        f"<b>Appuntamenti in cache</b> · totale {result.total}"
        if result.total
        else texts.EMPTY_PAGE,
        build_list(
            entries,
            namespace=NS,
            page_action=A_APPOINTMENTS,
            page=result.page,
            pages=result.pages,
            back_target=callbacks.build(NS, A_ROOT),
        ),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_APPOINTMENT}"))
async def open_appointment(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    appointment = await AppointmentRepository(session).get_any(data.uuid_arg(0))
    if appointment is None:
        raise NotFoundError("appointment not found")
    lines = [
        f"<b>{format_local_datetime(appointment.scheduled_at_utc)}</b>",
        f"Prestazione: {escape(appointment.service_name or '-')}",
        f"Sede: {escape(appointment.location_name or '-')}",
        f"Paziente: {escape(mask_person_name(appointment.patient_label or ''))}",
        f"Stato: <b>{appointment.status.value.lower()}</b>",
        f"ID portale: <code>{escape(appointment.portal_external_id[:6])}…</code>",
    ]
    rows = [
        [
            Entry(
                "🤝 Disdici per conto dell'utente",
                callbacks.build(NS, A_DELEGATED, "cancel", appointment.id),
            )
        ],
        [
            Entry(
                "📄 Promemoria per conto dell'utente",
                callbacks.build(NS, A_DELEGATED, "pdf", appointment.id),
            )
        ],
    ]
    await render(
        query,
        "\n".join(lines),
        build_actions(rows, back_target=callbacks.build(NS, A_APPOINTMENTS, 0)),
    )


# --- notifications -----------------------------------------------------------
@router.callback_query(F.data.startswith(f"{NS}|{A_NOTIFICATIONS}"))
async def list_notifications(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    result = await container.admin.list_notifications(
        session, user, page=data.int_arg(0), page_size=page_size(user)
    )
    lines = [
        f"• {escape(item.event_type.value)} · <code>{escape(item.status.value)}</code> · "
        f"tentativi {item.attempt_count} · {format_local_datetime(item.created_at)}"
        for item in result.items
    ]
    await render(
        query,
        f"<b>Notifiche</b> · totale {result.total}\n\n"
        + ("\n".join(lines) if lines else texts.EMPTY_PAGE),
        build_list(
            [],
            namespace=NS,
            page_action=A_NOTIFICATIONS,
            page=result.page,
            pages=result.pages,
            back_target=callbacks.build(NS, A_ROOT),
        ),
    )


# --- system events and breakers ---------------------------------------------
@router.callback_query(F.data.startswith(f"{NS}|{A_EVENTS}"))
async def list_events(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    result = await container.admin.list_events(
        session, user, page=data.int_arg(0), page_size=page_size(user), only_unresolved=True
    )
    entries = [
        Entry(
            f"{item.severity.value} · {item.event_code} · {format_local_datetime(item.created_at)}",
            callbacks.build(NS, A_EVENT_RESOLVE, item.id),
        )
        for item in result.items
    ]
    details = "\n".join(
        f"• <code>{escape(item.event_code)}</code> {escape(item.message[:120])}"
        for item in result.items
    )
    await render(
        query,
        f"<b>Errori non risolti</b> · totale {result.total}\n\n"
        + (details if details else texts.EMPTY_PAGE),
        build_list(
            entries,
            namespace=NS,
            page_action=A_EVENTS,
            page=result.page,
            pages=result.pages,
            back_target=callbacks.build(NS, A_ROOT),
            header_rows=[[_btn("🔌 Circuit breaker", callbacks.build(NS, A_BREAKERS, 0))]],
        ),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_BREAKERS}"))
async def list_breakers(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    status = await container.admin.system_status(session, user)
    lines = [
        f"• {item['scope']} <code>{escape(str(item['subject'])[:12])}</code> → "
        f"<b>{item['state']}</b> (errori {item['failures']})"
        for item in status.breakers
    ]
    rows = [
        [
            Entry(
                "🔒 Apri globale",
                callbacks.build(
                    NS, A_BREAKER_SET, BreakerScope.GLOBAL.value, "GLOBAL", BreakerState.OPEN.value
                ),
            ),
            Entry(
                "🔓 Chiudi globale",
                callbacks.build(
                    NS,
                    A_BREAKER_SET,
                    BreakerScope.GLOBAL.value,
                    "GLOBAL",
                    BreakerState.CLOSED.value,
                ),
            ),
        ]
    ]
    await render(
        query,
        f"{texts.ADMIN_BREAKER_TITLE}\n\n" + ("\n".join(lines) if lines else texts.EMPTY_PAGE),
        build_actions(rows, back_target=callbacks.build(NS, A_EVENTS, 0)),
    )


# --- jobs and leases ---------------------------------------------------------
@router.callback_query(F.data.startswith(f"{NS}|{A_JOBS}"))
async def show_jobs(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    status = await container.admin.system_status(session, user)
    workers = "\n".join(
        f"• <code>{escape(str(item['worker_id'])[:24])}</code> · "
        f"{'STALE' if item['stale'] else 'ok'} · {escape(str(item['last_seen_at']))}"
        for item in status.workers
    )
    text = "\n".join(
        [
            "<b>Code e lease</b>",
            f"Monitoraggi attivi: <b>{status.watches_active}</b>",
            f"Monitoraggi in attesa: <b>{status.watches_due}</b>",
            f"Lease attivi: <b>{status.watches_leased}</b>",
            f"Notifiche in coda: <b>{status.notifications_pending}</b>",
            f"Notifiche fallite: <b>{status.notifications_failed}</b>",
            f"Chiamate al portale (ultimo minuto): <b>{status.portal_calls_last_minute}</b>",
            "",
            "<b>Worker</b>",
            workers or texts.EMPTY_PAGE,
        ]
    )
    await render(query, text, build_actions([], back_target=callbacks.build(NS, A_ROOT)))


# --- audit -------------------------------------------------------------------
@router.callback_query(F.data.startswith(f"{NS}|{A_AUDIT}"))
async def list_audit(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    result = await container.admin.list_audit(
        session, user, page=data.int_arg(0), page_size=page_size(user)
    )
    lines = [
        f"• {format_local_datetime(item.created_at)} · <code>{escape(item.action.value)}</code> "
        f"· {escape(item.target_type)} · {escape((item.reason or '-')[:60])}"
        for item in result.items
    ]
    await render(
        query,
        f"<b>Audit</b> · totale {result.total}\n\n"
        + ("\n".join(lines) if lines else texts.EMPTY_PAGE),
        build_list(
            [],
            namespace=NS,
            page_action=A_AUDIT,
            page=result.page,
            pages=result.pages,
            back_target=callbacks.build(NS, A_ROOT),
        ),
    )


# --- system status -----------------------------------------------------------
@router.callback_query(F.data.startswith(f"{NS}|{A_SYSTEM}"))
async def show_system(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    status = await container.admin.system_status(session, user)
    users_summary = " · ".join(
        f"{key.lower()}: {value}" for key, value in status.users_by_status.items() if value
    )
    backup = status.last_backup
    text = "\n".join(
        [
            texts.ADMIN_SYSTEM_TITLE,
            f"Versione applicativa: <b>{escape(status.app_version)}</b>",
            f"Schema database: <b>{escape(status.schema_version)}</b>",
            "",
            f"Utenti: {escape(users_summary or '-')}",
            f"Account portale: <b>{status.accounts_total}</b> "
            f"(da riautenticare: <b>{status.accounts_needing_reauth}</b>)",
            f"Monitoraggi attivi: <b>{status.watches_active}</b>",
            f"Eventi non risolti: <b>{status.unresolved_events}</b> "
            f"(critici: <b>{status.critical_events}</b>)",
            f"Worker registrati: <b>{len(status.workers)}</b>",
            f"Ultimo backup: <b>{escape(str(backup['outcome'])) if backup else 'mai eseguito'}</b>",
            "",
            texts.ADMIN_NO_SECRETS,
        ]
    )
    await render(query, text, build_actions([], back_target=callbacks.build(NS, A_ROOT)))


# --- monitor diagnostics -----------------------------------------------------
@router.callback_query(F.data.startswith(f"{NS}|{A_SEEN_SLOTS}"))
async def list_seen_slots(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    """Slots already observed by a monitor, with no personal data."""
    await acknowledge(query)
    data = callbacks.parse(query.data)
    watch_id = data.uuid_arg(1)
    result = await container.admin.list_seen_slots(
        session, user, watch_id=watch_id, page=data.int_arg(0), page_size=page_size(user)
    )
    lines = [
        f"• {format_local_datetime(item.slot_starts_at)} · "
        f"{escape(item.location_name or '-')} · "
        f"{'disponibile' if item.currently_available else 'non più disponibile'}"
        for item in result.items
    ]
    await render(
        query,
        f"<b>Slot visti</b> · totale {result.total}\n\n"
        + ("\n".join(lines) if lines else texts.EMPTY_PAGE),
        build_list(
            [],
            namespace=NS,
            page_action=A_SEEN_SLOTS,
            page=result.page,
            pages=result.pages,
            extra=(watch_id,),
            back_target=callbacks.build(NS, A_WATCH, watch_id),
        ),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_RUN_LOGS}"))
async def list_run_logs(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    watch_id = data.uuid_arg(1)
    result = await container.admin.list_watch_runs(
        session, user, watch_id=watch_id, page=data.int_arg(0), page_size=page_size(user)
    )
    lines = [
        f"• {format_local_datetime(item.started_at)} · <code>{escape(item.outcome)}</code> · "
        f"slot {item.slots_found} (nuovi {item.new_slots}) · "
        f"<code>{escape(item.error_code or '-')}</code>"
        for item in result.items
    ]
    await render(
        query,
        f"<b>Esecuzioni</b> · totale {result.total}\n\n"
        + ("\n".join(lines) if lines else texts.EMPTY_PAGE),
        build_list(
            [],
            namespace=NS,
            page_action=A_RUN_LOGS,
            page=result.page,
            pages=result.pages,
            extra=(watch_id,),
            back_target=callbacks.build(NS, A_WATCH, watch_id),
        ),
    )


@router.message(Command("invite"))
async def invite_command(message: Message, state: FSMContext) -> None:
    """``/invite`` is the shortcut to the invite creation flow."""
    await state.set_state(AdminStates.waiting_invite_uses)
    await message.answer(texts.ADMIN_INVITE_ASK_USES)
