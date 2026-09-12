"""Portal account screens: connect, test, rename, sync, disable and remove."""

from __future__ import annotations

import uuid

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import callbacks, texts
from app.bot.keyboards.inline import Entry, build_actions, build_list
from app.bot.presenters.errors import describe_error
from app.bot.presenters.formatting import escape, format_account, format_patient
from app.bot.routers.common import acknowledge, page_size, render, send_new
from app.composition import Container
from app.db.models.users import TelegramUser
from app.db.repositories.accounts import PatientRepository
from app.domain.enums import ConfirmationAction, PortalAccountStatus
from app.domain.errors import (
    AuthenticationFailed,
    DomainError,
    NotFoundError,
    ValidationError,
)
from app.logging import get_logger
from app.services.accounts import clean_email

logger = get_logger(__name__)
router = Router(name="accounts")

NS = callbacks.ACCOUNT
A_LIST = "list"
A_OPEN = "open"
A_CONNECT = "conn"
A_REAUTH = "reauth"
A_TEST = "test"
A_SYNC = "sync"
A_RENAME = "rename"
A_TOGGLE = "toggle"
A_REMOVE = "remove"
A_REMOVE_OK = "rmok"
A_PATIENTS = "pat"


class AccountStates(StatesGroup):
    """Free-text steps of the account screens."""

    waiting_label = State()
    waiting_new_label = State()
    waiting_email = State()
    waiting_password = State()


def _list_target() -> str:
    return callbacks.build(NS, A_LIST, 0)


async def _render_accounts(
    event: CallbackQuery | Message,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    *,
    page: int,
) -> None:
    accounts = await container.accounts.list_accounts(session, user)
    size = page_size(user)
    pages = max(1, -(-len(accounts) // size))
    current = min(max(page, 0), pages - 1)
    visible = accounts[current * size : (current + 1) * size]
    entries = [
        Entry(format_account(account), callbacks.build(NS, A_OPEN, account.id))
        for account in visible
    ]
    keyboard = build_list(
        entries,
        namespace=NS,
        page_action=A_LIST,
        page=current,
        pages=pages,
        header_rows=[[_button(texts.ACCOUNT_CONNECT_BUTTON, callbacks.build(NS, A_CONNECT))]],
    )
    text = texts.ACCOUNTS_TITLE if accounts else texts.ACCOUNTS_EMPTY
    await render(event, text, keyboard)


def _button(label: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=label, callback_data=data)


@router.message(F.text == texts.BTN_ACCOUNTS)
async def open_accounts(
    message: Message, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await _render_accounts(message, session, container, user, page=0)


@router.callback_query(F.data.startswith(f"{NS}|{A_LIST}"))
async def list_accounts(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    await _render_accounts(query, session, container, user, page=data.int_arg(0))


@router.callback_query(F.data.startswith(f"{NS}|{A_CONNECT}"))
async def start_connection(
    query: CallbackQuery,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    state: FSMContext,
) -> None:
    """Begin the in-chat credential flow after checking consent and quota."""
    await acknowledge(query)
    await container.accounts.ensure_can_connect(session, user)
    await state.set_state(AccountStates.waiting_label)
    await state.update_data(reauth_account_id=None)
    await render(
        query,
        texts.ACCOUNT_ASK_LABEL,
        build_actions([], back_target=_list_target(), show_cancel=True),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_REAUTH}"))
async def start_reauthentication(
    query: CallbackQuery,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    state: FSMContext,
) -> None:
    """Refresh the credentials of an existing account, in chat."""
    await acknowledge(query)
    data = callbacks.parse(query.data)
    account = await container.accounts.require_account(session, user, data.uuid_arg(0))
    await state.set_state(AccountStates.waiting_email)
    await state.update_data(reauth_account_id=account.id.hex, label=account.label)
    await render(
        query,
        texts.ACCOUNT_REAUTH_START.format(label=escape(account.label)),
        build_actions([], back_target=callbacks.build(NS, A_OPEN, account.id), show_cancel=True),
    )


@router.message(AccountStates.waiting_label)
async def receive_label(message: Message, state: FSMContext) -> None:
    """Store the label, then ask for the e-mail address."""
    await state.update_data(label=(message.text or "").strip())
    await state.set_state(AccountStates.waiting_email)
    await message.answer(
        texts.ACCOUNT_ASK_EMAIL,
        reply_markup=build_actions([], back_target=_list_target(), show_cancel=True),
        parse_mode="HTML",
    )


@router.message(AccountStates.waiting_email)
async def receive_email(message: Message, state: FSMContext) -> None:
    """Validate and store the e-mail, then ask for the password."""
    email = clean_email(message.text or "")
    await state.update_data(email=email)
    await state.set_state(AccountStates.waiting_password)
    await message.answer(
        texts.ACCOUNT_ASK_PASSWORD,
        reply_markup=build_actions([], show_cancel=True),
        parse_mode="HTML",
    )


async def _erase(message: Message) -> bool:
    """Delete the message carrying the password, as soon as it is read."""
    try:
        await message.delete()
    except TelegramBadRequest:
        logger.warning(
            "could not delete the credential message",
            extra={"event_code": "CREDENTIAL_MESSAGE_NOT_DELETED"},
        )
        return False
    return True


@router.message(AccountStates.waiting_password)
async def receive_password(
    message: Message,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    state: FSMContext,
    correlation_id: str = "",
) -> None:
    """Verify the credentials and store them, keeping nothing in the chat.

    The password lives only in this frame: it is never written to the
    conversation state and the message carrying it is deleted immediately.
    """
    password = message.text or ""
    erased = await _erase(message)
    stored = await state.get_data()
    await state.clear()
    await message.answer(
        texts.ACCOUNT_CREDENTIALS_DELETED if erased else texts.ACCOUNT_CREDENTIALS_NOT_DELETED
    )

    label = str(stored.get("label") or "")
    email = str(stored.get("email") or "")
    raw_account_id = stored.get("reauth_account_id")
    if not email:
        raise NotFoundError("the connection flow expired")

    notice = await message.answer(texts.ACCOUNT_CONNECTING)
    try:
        if raw_account_id:
            account = await container.accounts.require_account(
                session, user, uuid.UUID(hex=str(raw_account_id))
            )
            await container.accounts.reauthenticate(
                session, user, account, email=email, password=password
            )
            await notice.edit_text(texts.ACCOUNT_REAUTH_DONE, parse_mode="HTML")
        else:
            account = await container.accounts.connect(
                session, user, label=label, email=email, password=password
            )
            patients = await PatientRepository(session).list_for_account(account.id)
            await notice.edit_text(
                texts.ACCOUNT_CONNECTED.format(label=escape(account.label), patients=len(patients)),
                parse_mode="HTML",
            )
    except AuthenticationFailed:
        await notice.edit_text(texts.ACCOUNT_CONNECT_FAILED, parse_mode="HTML")
        return
    except DomainError as exc:
        # Not the password, the portal. Answering where the user is already
        # looking beats leaving "checking your credentials" on screen for ever
        # while the explanation arrives underneath it as a separate message.
        logger.info(
            "the credentials could not be checked",
            extra={"event_code": exc.code, "correlation_id": correlation_id},
        )
        await notice.edit_text(describe_error(exc, correlation_id), parse_mode="HTML")
        return
    finally:
        del password

    await _render_account(message, session, container, user, account.id)


def _account_actions(account_id: uuid.UUID, *, enabled: bool) -> list[list[Entry]]:
    toggle_label = "⏸ Disabilita" if enabled else "▶️ Riabilita"
    return [
        [
            Entry("🔍 Test accesso", callbacks.build(NS, A_TEST, account_id)),
            Entry("👥 Pazienti", callbacks.build(NS, A_PATIENTS, account_id, 0)),
        ],
        [
            Entry("🔄 Sincronizza pazienti", callbacks.build(NS, A_SYNC, account_id)),
            Entry("✏️ Rinomina", callbacks.build(NS, A_RENAME, account_id)),
        ],
        [
            Entry("🔐 Aggiorna credenziali", callbacks.build(NS, A_REAUTH, account_id)),
            Entry(toggle_label, callbacks.build(NS, A_TOGGLE, account_id)),
        ],
        [Entry("🗑 Rimuovi account", callbacks.build(NS, A_REMOVE, account_id))],
    ]


async def _render_account(
    event: CallbackQuery | Message,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    account_id: uuid.UUID,
    *,
    notice: str = "",
) -> None:
    account = await container.accounts.require_account(session, user, account_id)
    patients = await PatientRepository(session).list_for_account(account.id)
    lines = [
        f"<b>{escape(account.label)}</b>",
        "Stato: <b>"
        + texts.ACCOUNT_STATUS_LABELS.get(account.status.value, account.status.value)
        + "</b>",
        f"Pazienti sincronizzati: <b>{len(patients)}</b>",
        f"Ultimo accesso: {account.last_login_at or 'mai'}",
        f"Ultima sincronizzazione: {account.last_sync_at or 'mai'}",
    ]
    if account.last_error_code:
        lines.append(f"Ultimo errore: <code>{escape(account.last_error_code)}</code>")
    if notice:
        lines.extend(["", notice])
    await render(
        event,
        "\n".join(lines),
        build_actions(
            _account_actions(account.id, enabled=account.status is PortalAccountStatus.ACTIVE),
            back_target=_list_target(),
        ),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_OPEN}"))
async def open_account(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    await _render_account(query, session, container, user, data.uuid_arg(0))


@router.callback_query(F.data.startswith(f"{NS}|{A_TEST}"))
async def test_account(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    account = await container.accounts.require_account(session, user, data.uuid_arg(0))
    verified = await container.accounts.test_login(session, account)
    await _render_account(
        query,
        session,
        container,
        user,
        account.id,
        notice=texts.ACCOUNT_TEST_OK if verified else texts.ACCOUNT_TEST_FAILED,
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_SYNC}"))
async def sync_patients(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    account = await container.accounts.require_account(session, user, data.uuid_arg(0))
    result = await container.accounts.sync_patients(session, account)
    await _render_account(
        query,
        session,
        container,
        user,
        account.id,
        notice=texts.PATIENTS_SYNCED.format(
            count=result.synchronised, deactivated=result.deactivated
        ),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_PATIENTS}"))
async def list_patients(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    """Patients are listed with masked identifiers; tax codes are never shown."""
    await acknowledge(query)
    data = callbacks.parse(query.data)
    account = await container.accounts.require_account(session, user, data.uuid_arg(1))
    patients = await PatientRepository(session).list_for_account(account.id)
    size = page_size(user)
    pages = max(1, -(-len(patients) // size))
    current = min(max(data.int_arg(0), 0), pages - 1)
    visible = patients[current * size : (current + 1) * size]
    entries = [Entry(format_patient(patient), callbacks.noop()) for patient in visible]
    await render(
        query,
        f"<b>Pazienti di {escape(account.label)}</b>" if patients else texts.PATIENTS_EMPTY,
        build_list(
            entries,
            namespace=NS,
            page_action=A_PATIENTS,
            page=current,
            pages=pages,
            extra=(account.id,),
            back_target=callbacks.build(NS, A_OPEN, account.id),
        ),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_RENAME}"))
async def ask_label(
    query: CallbackQuery,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    state: FSMContext,
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    account = await container.accounts.require_account(session, user, data.uuid_arg(0))
    await state.set_state(AccountStates.waiting_new_label)
    await state.update_data(account_id=account.id.hex)
    await render(
        query,
        texts.ACCOUNT_RENAME_PROMPT,
        build_actions([], back_target=callbacks.build(NS, A_OPEN, account.id), show_cancel=True),
    )


@router.message(AccountStates.waiting_new_label)
async def apply_label(
    message: Message,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    state: FSMContext,
) -> None:
    stored = await state.get_data()
    raw_id = stored.get("account_id")
    if not raw_id:
        await state.clear()
        raise NotFoundError("account not found")
    account = await container.accounts.require_account(session, user, uuid.UUID(hex=raw_id))
    label = (message.text or "").strip()
    if not label:
        raise ValidationError("a label is required")
    await container.accounts.rename(session, account, label=label)
    await state.clear()
    await message.answer(texts.ACCOUNT_RENAMED)
    await _render_account(message, session, container, user, account.id)


@router.callback_query(F.data.startswith(f"{NS}|{A_TOGGLE}"))
async def toggle_account(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    account = await container.accounts.require_account(session, user, data.uuid_arg(0))
    enabled = account.status is not PortalAccountStatus.ACTIVE
    affected = await container.accounts.set_enabled(session, account, enabled=enabled)
    notice = texts.ACCOUNT_ENABLED if enabled else texts.ACCOUNT_DISABLED
    if not enabled and affected:
        notice = f"{notice} Monitoraggi sospesi: <b>{affected}</b>."
    await _render_account(query, session, container, user, account.id, notice=notice)


@router.callback_query(F.data.startswith(f"{NS}|{A_REMOVE}"))
async def confirm_remove(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    """First confirmation step: show the impact and issue a fresh nonce."""
    await acknowledge(query)
    data = callbacks.parse(query.data)
    account = await container.accounts.require_account(session, user, data.uuid_arg(0))
    watches = await container.accounts.watches_affected_by_removal(session, account)
    confirmation = await container.confirmations.issue(
        session,
        user=user,
        action=ConfirmationAction.DELETE_ACCOUNT,
        target_type="portal_account",
        target_id=account.id.hex,
        context_parts=(account.id.hex, account.label, str(watches)),
    )
    await render(
        query,
        texts.ACCOUNT_REMOVE_WARNING.format(watches=watches),
        build_actions(
            [
                [
                    Entry(
                        "🗑 Conferma rimozione",
                        callbacks.build(NS, A_REMOVE_OK, account.id, confirmation.nonce),
                    )
                ]
            ],
            back_target=callbacks.build(NS, A_OPEN, account.id),
            show_cancel=True,
        ),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_REMOVE_OK}"))
async def remove_account(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    """Second confirmation step: consume the nonce and remove the account."""
    await acknowledge(query)
    data = callbacks.parse(query.data)
    account = await container.accounts.require_account(session, user, data.uuid_arg(0))
    watches = await container.accounts.watches_affected_by_removal(session, account)
    await container.confirmations.consume(
        session,
        user=user,
        action=ConfirmationAction.DELETE_ACCOUNT,
        nonce=data.arg(1),
        context_parts=(account.id.hex, account.label, str(watches)),
    )
    removed = await container.accounts.remove(session, account)
    await render(query, f"{texts.ACCOUNT_REMOVED} Monitoraggi eliminati: <b>{removed}</b>.")
    await send_new(query, texts.ACCOUNTS_TITLE)
    await _render_accounts(query, session, container, user, page=0)
