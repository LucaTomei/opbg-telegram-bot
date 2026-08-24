"""Monitor screens: creation from a draft, listing, editing and deletion."""

from __future__ import annotations

import uuid

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import callbacks, texts
from app.bot.keyboards.inline import Entry, build_actions, build_list
from app.bot.presenters.formatting import (
    format_excluded_dates,
    format_time_windows,
    format_watch_detail,
    format_watch_row,
    format_watch_summary,
)
from app.bot.routers.common import acknowledge, page_size, render
from app.composition import Container
from app.db.models.users import TelegramUser
from app.db.models.watches import BookingWatch
from app.db.repositories.conversations import BookingDraftRepository
from app.db.repositories.watches import WatchRepository
from app.domain.enums import (
    ConfirmationAction,
    WatchCompletionPolicy,
    WatchMode,
    WatchStatus,
)
from app.domain.errors import NotFoundError, RateLimitedError, ValidationError
from app.domain.filters import parse_excluded_dates, parse_time_windows
from app.logging import get_logger
from app.services.mapping import copy_watch_into_draft

logger = get_logger(__name__)
router = Router(name="watches")

NS = callbacks.WATCH
A_LIST = "list"
A_OPEN = "open"
A_MODE = "mode"
A_NOTIFY_MODE = "nm"
A_INTERVAL = "int"
A_POLICY = "pol"
A_CREATE = "create"
A_PAUSE = "pause"
A_RESUME = "resume"
A_TOGGLE_NOTIF = "tn"
A_SET_MODE = "sm"
A_RUN = "run"
A_DUPLICATE = "dup"
A_RENAME = "ren"
A_DELETE = "del"
A_DELETE_OK = "delok"
A_EDIT = "edit"
A_INTERVAL_SET = "iset"

INTERVAL_CHOICES: tuple[int, ...] = (120, 300, 600, 1800, 3600, 21600)


class WatchStates(StatesGroup):
    """Free-text steps of the monitor screens."""

    waiting_name = State()
    waiting_new_name = State()


# --- creation from a booking draft -------------------------------------------
@router.callback_query(F.data.startswith(f"{callbacks.BOOKING}|tw"))
async def start_from_draft(
    query: CallbackQuery,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    state: FSMContext,
) -> None:
    """Reuse the account, patient, search, locations and filters of the draft."""
    await acknowledge(query)
    data = callbacks.parse(query.data)
    draft = await BookingDraftRepository(session).get_owned(data.uuid_arg(0), user.id)
    if draft is None:
        raise NotFoundError("booking draft expired")
    await state.set_state(WatchStates.waiting_name)
    await state.update_data(draft_id=draft.id.hex)
    await render(
        query,
        texts.WATCH_ASK_NAME,
        build_actions(
            [],
            back_target=callbacks.build(callbacks.BOOKING, "slots", draft.id, 0),
            show_cancel=True,
        ),
    )


@router.message(WatchStates.waiting_name)
async def ask_mode(
    message: Message,
    session: AsyncSession,
    user: TelegramUser,
    state: FSMContext,
) -> None:
    stored = await state.get_data()
    raw_id = stored.get("draft_id")
    if not raw_id:
        await state.clear()
        raise NotFoundError("booking draft expired")
    name = " ".join((message.text or "").split())[:80]
    if not name:
        raise ValidationError("a name is required")
    await state.update_data(name=name)
    await state.set_state(state=None)
    draft_id = uuid.UUID(hex=str(raw_id))
    await message.answer(
        texts.WATCH_ASK_MODE,
        reply_markup=build_actions(
            [
                [Entry("🔔 Solo notifica", callbacks.build(NS, A_MODE, draft_id, "N"))],
                [Entry("🤖 Auto-prenota", callbacks.build(NS, A_MODE, draft_id, "A"))],
                [Entry("🤖🔔 Auto-prenota + notifica", callbacks.build(NS, A_MODE, draft_id, "B"))],
            ],
            show_cancel=True,
        ),
        parse_mode="HTML",
    )


_MODE_CODES: dict[str, WatchMode] = {
    "N": WatchMode.NOTIFY,
    "A": WatchMode.AUTOBOOK,
    "B": WatchMode.AUTOBOOK_NOTIFY,
}


@router.callback_query(F.data.startswith(f"{NS}|{A_MODE}"))
async def choose_mode(query: CallbackQuery, user: TelegramUser, state: FSMContext) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    mode = _MODE_CODES.get(data.arg(1))
    if mode is None:
        raise ValidationError("unknown monitor mode")
    await state.update_data(mode=mode.value)
    draft_id = data.uuid_arg(0)
    await render(
        query,
        texts.WATCH_ASK_NOTIFY_MODE,
        build_actions(
            [
                [Entry("🆕 Solo nuovi slot", callbacks.build(NS, A_NOTIFY_MODE, draft_id, "1"))],
                [
                    Entry(
                        "📋 Riepilogo a ogni controllo",
                        callbacks.build(NS, A_NOTIFY_MODE, draft_id, "0"),
                    )
                ],
                [Entry("🔕 Nessuna notifica", callbacks.build(NS, A_NOTIFY_MODE, draft_id, "off"))],
            ],
            show_cancel=True,
        ),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_NOTIFY_MODE}"))
async def choose_notify_mode(
    query: CallbackQuery, user: TelegramUser, state: FSMContext, container: Container
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    choice = data.arg(1)
    await state.update_data(
        notifications_enabled=choice != "off",
        notify_only_changes=choice != "0",
    )
    draft_id = data.uuid_arg(0)
    minimum = container.settings.monitor_min_interval_seconds
    rows = [
        [
            Entry(
                f"{seconds // 60} min" if seconds >= 60 else f"{seconds} s",
                callbacks.build(NS, A_INTERVAL, draft_id, seconds),
            )
            for seconds in group
        ]
        for group in (INTERVAL_CHOICES[:2], INTERVAL_CHOICES[2:4], INTERVAL_CHOICES[4:])
    ]
    await render(
        query,
        texts.WATCH_ASK_INTERVAL.format(minimum=minimum),
        build_actions(rows, show_cancel=True),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_INTERVAL}"))
async def choose_interval(query: CallbackQuery, user: TelegramUser, state: FSMContext) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    await state.update_data(interval_seconds=data.int_arg(1, 300))
    draft_id = data.uuid_arg(0)
    await render(
        query,
        texts.WATCH_ASK_COMPLETION,
        build_actions(
            [
                [Entry("✅ Completa e disattiva", callbacks.build(NS, A_POLICY, draft_id, "stop"))],
                [Entry("🔁 Continua a monitorare", callbacks.build(NS, A_POLICY, draft_id, "go"))],
            ],
            show_cancel=True,
        ),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_POLICY}"))
async def choose_policy(
    query: CallbackQuery,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    state: FSMContext,
) -> None:
    """Ask for an extra confirmation when auto-booking is enabled."""
    await acknowledge(query)
    data = callbacks.parse(query.data)
    policy = (
        WatchCompletionPolicy.CONTINUE_AFTER_SUCCESS
        if data.arg(1) == "go"
        else WatchCompletionPolicy.COMPLETE_ON_SUCCESS
    )
    await state.update_data(completion_policy=policy.value)
    stored = await state.get_data()
    draft_id = data.uuid_arg(0)
    mode = WatchMode(str(stored.get("mode", WatchMode.NOTIFY.value)))
    if mode.books:
        confirmation = await container.confirmations.issue(
            session,
            user=user,
            action=ConfirmationAction.ENABLE_AUTOBOOK,
            target_type="booking_draft",
            target_id=draft_id.hex,
            context_parts=(draft_id.hex, mode.value, policy.value),
        )
        await render(
            query,
            texts.WATCH_AUTOBOOK_WARNING,
            build_actions(
                [
                    [
                        Entry(
                            "✅ Confermo l'auto-prenotazione",
                            callbacks.build(NS, A_CREATE, draft_id, confirmation.nonce),
                        )
                    ]
                ],
                show_cancel=True,
            ),
        )
        return
    await _create_watch(query, session, container, user, state, draft_id, nonce=None)


@router.callback_query(F.data.startswith(f"{NS}|{A_CREATE}"))
async def create_confirmed(
    query: CallbackQuery,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    state: FSMContext,
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    await _create_watch(query, session, container, user, state, data.uuid_arg(0), nonce=data.arg(1))


async def _create_watch(
    query: CallbackQuery,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    state: FSMContext,
    draft_id: uuid.UUID,
    *,
    nonce: str | None,
) -> None:
    stored = await state.get_data()
    draft = await BookingDraftRepository(session).get_owned(draft_id, user.id)
    if draft is None:
        await state.clear()
        raise NotFoundError("booking draft expired")
    mode = WatchMode(str(stored.get("mode", WatchMode.NOTIFY.value)))
    policy = WatchCompletionPolicy(
        str(stored.get("completion_policy", WatchCompletionPolicy.COMPLETE_ON_SUCCESS.value))
    )
    if nonce is not None:
        await container.confirmations.consume(
            session,
            user=user,
            action=ConfirmationAction.ENABLE_AUTOBOOK,
            nonce=nonce,
            context_parts=(draft_id.hex, mode.value, policy.value),
        )
    watch = await container.watches.create_from_draft(
        session,
        user=user,
        draft=draft,
        name=str(stored.get("name", "Monitoraggio")),
        mode=mode,
        notifications_enabled=bool(stored.get("notifications_enabled", True)),
        notify_only_changes=bool(stored.get("notify_only_changes", True)),
        interval_seconds=int(stored.get("interval_seconds", 300)),
        completion_policy=policy,
    )
    await state.clear()
    await render(query, texts.WATCH_CREATED)
    await _render_watch(query, session, container, user, watch.id)


# --- listing -----------------------------------------------------------------
async def _render_watches(
    event: CallbackQuery | Message,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    *,
    page: int,
) -> None:
    result = await container.watches.list_for_user(
        session, user, page=page, page_size=page_size(user)
    )
    entries = [
        Entry(format_watch_row(watch), callbacks.build(NS, A_OPEN, watch.id))
        for watch in result.items
    ]
    summaries: list[str] = []
    for watch in result.items:
        summary = await container.watches.summarise(session, watch)
        summaries.append(
            format_watch_summary(
                watch,
                account_label=summary.account_label,
                patient_label=summary.patient_label,
                location_summary=summary.location_summary,
            )
        )
    body = (
        f"{texts.WATCHES_TITLE}\n\n" + "\n\n".join(summaries)
        if result.total
        else texts.WATCHES_EMPTY
    )
    await render(
        event,
        body,
        build_list(
            entries,
            namespace=NS,
            page_action=A_LIST,
            page=result.page,
            pages=result.pages,
        ),
    )


@router.message(F.text == texts.BTN_WATCHES)
async def open_watches(
    message: Message, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await _render_watches(message, session, container, user, page=0)


@router.callback_query(F.data.startswith(f"{NS}|{A_LIST}"))
async def list_watches(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    await _render_watches(query, session, container, user, page=data.int_arg(0))


def _watch_actions(watch: BookingWatch) -> list[list[Entry]]:
    paused = watch.status is not WatchStatus.ACTIVE
    toggle = (
        Entry("▶️ Riprendi", callbacks.build(NS, A_RESUME, watch.id))
        if paused
        else Entry("⏸ Pausa", callbacks.build(NS, A_PAUSE, watch.id))
    )
    bell = (
        Entry("🔔 Attiva notifiche", callbacks.build(NS, A_TOGGLE_NOTIF, watch.id))
        if not watch.notifications_enabled
        else Entry("🔕 Disattiva notifiche", callbacks.build(NS, A_TOGGLE_NOTIF, watch.id))
    )
    autobook = (
        Entry("🤖 Auto-prenota ON", callbacks.build(NS, A_SET_MODE, watch.id, "on"))
        if not watch.mode.books
        else Entry("🤖 Auto-prenota OFF", callbacks.build(NS, A_SET_MODE, watch.id, "off"))
    )
    return [
        [Entry("▶️ Esegui ora", callbacks.build(NS, A_RUN, watch.id)), toggle],
        [bell, autobook],
        [
            Entry("🛠 Modifica filtri", callbacks.build(NS, A_EDIT, watch.id)),
            Entry("⏱ Frequenza", callbacks.build(NS, A_INTERVAL_SET, watch.id)),
        ],
        [
            Entry("📄 Duplica", callbacks.build(NS, A_DUPLICATE, watch.id)),
            Entry("✏️ Rinomina", callbacks.build(NS, A_RENAME, watch.id)),
        ],
        [Entry("🗑 Elimina", callbacks.build(NS, A_DELETE, watch.id))],
    ]


async def _render_watch(
    event: CallbackQuery | Message,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    watch_id: uuid.UUID,
    *,
    notice: str = "",
) -> None:
    watch = await container.watches.require_watch(session, user, watch_id)
    summary = await container.watches.summarise(session, watch)
    detail = format_watch_detail(
        watch,
        account_label=summary.account_label,
        patient_label=summary.patient_label,
        location_summary=summary.location_summary,
        time_windows=format_time_windows(parse_time_windows(watch.time_windows_json)),
        excluded=format_excluded_dates(sorted(parse_excluded_dates(watch.excluded_dates_json))),
    )
    if notice:
        detail = f"{detail}\n\n{notice}"
    await render(
        event,
        detail,
        build_actions(_watch_actions(watch), back_target=callbacks.build(NS, A_LIST, 0)),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_OPEN}"))
async def open_watch(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    await _render_watch(query, session, container, user, data.uuid_arg(0))


@router.callback_query(F.data.startswith(f"{NS}|{A_PAUSE}"))
async def pause_watch(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    watch = await container.watches.require_watch(session, user, data.uuid_arg(0))
    await container.watches.pause(session, watch)
    await _render_watch(query, session, container, user, watch.id, notice=texts.WATCH_PAUSED)


@router.callback_query(F.data.startswith(f"{NS}|{A_RESUME}"))
async def resume_watch(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    watch = await container.watches.require_watch(session, user, data.uuid_arg(0))
    await container.watches.resume(session, watch)
    await _render_watch(query, session, container, user, watch.id, notice=texts.WATCH_RESUMED)


@router.callback_query(F.data.startswith(f"{NS}|{A_TOGGLE_NOTIF}"))
async def toggle_notifications(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    """Notifications are toggled independently from auto-booking."""
    await acknowledge(query)
    data = callbacks.parse(query.data)
    watch = await container.watches.require_watch(session, user, data.uuid_arg(0))
    enabled = not watch.notifications_enabled
    await container.watches.set_notifications(session, watch, enabled=enabled)
    await _render_watch(
        query,
        session,
        container,
        user,
        watch.id,
        notice=texts.WATCH_NOTIFICATIONS_ON if enabled else texts.WATCH_NOTIFICATIONS_OFF,
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_SET_MODE}"))
async def set_mode(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    """Enabling auto-booking always requires an explicit confirmation."""
    await acknowledge(query)
    data = callbacks.parse(query.data)
    watch = await container.watches.require_watch(session, user, data.uuid_arg(0))
    choice = data.arg(1)
    if choice == "off":
        await container.watches.set_mode(session, watch, mode=WatchMode.NOTIFY)
        await _render_watch(
            query, session, container, user, watch.id, notice="Auto-prenotazione disattivata."
        )
        return
    if choice == "on":
        confirmation = await container.confirmations.issue(
            session,
            user=user,
            action=ConfirmationAction.ENABLE_AUTOBOOK,
            target_type="booking_watch",
            target_id=watch.id.hex,
            context_parts=(watch.id.hex, "on"),
        )
        await render(
            query,
            texts.WATCH_AUTOBOOK_WARNING,
            build_actions(
                [
                    [
                        Entry(
                            "✅ Confermo",
                            callbacks.build(NS, A_SET_MODE, watch.id, confirmation.nonce),
                        )
                    ]
                ],
                back_target=callbacks.build(NS, A_OPEN, watch.id),
                show_cancel=True,
            ),
        )
        return
    await container.confirmations.consume(
        session,
        user=user,
        action=ConfirmationAction.ENABLE_AUTOBOOK,
        nonce=choice,
        context_parts=(watch.id.hex, "on"),
    )
    await container.watches.set_mode(session, watch, mode=WatchMode.AUTOBOOK_NOTIFY)
    await _render_watch(
        query, session, container, user, watch.id, notice="Auto-prenotazione attivata."
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_RUN}"))
async def run_now(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    watch = await container.watches.require_watch(session, user, data.uuid_arg(0))
    try:
        await container.watches.request_run_now(session, watch)
    except RateLimitedError as exc:
        await _render_watch(
            query,
            session,
            container,
            user,
            watch.id,
            notice=texts.WATCH_RUN_TOO_SOON.format(seconds=int(exc.retry_after_seconds)),
        )
        return
    await _render_watch(query, session, container, user, watch.id, notice=texts.WATCH_RUN_SCHEDULED)


@router.callback_query(F.data.startswith(f"{NS}|{A_DUPLICATE}"))
async def duplicate_watch(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    watch = await container.watches.require_watch(session, user, data.uuid_arg(0))
    clone = await container.watches.duplicate(session, watch)
    await render(query, texts.WATCH_DUPLICATED)
    await _render_watch(query, session, container, user, clone.id)


@router.callback_query(F.data.startswith(f"{NS}|{A_RENAME}"))
async def ask_new_name(
    query: CallbackQuery,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    state: FSMContext,
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    watch = await container.watches.require_watch(session, user, data.uuid_arg(0))
    await state.set_state(WatchStates.waiting_new_name)
    await state.update_data(watch_id=watch.id.hex)
    await render(
        query,
        texts.WATCH_ASK_NAME,
        build_actions([], back_target=callbacks.build(NS, A_OPEN, watch.id), show_cancel=True),
    )


@router.message(WatchStates.waiting_new_name)
async def apply_new_name(
    message: Message,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    state: FSMContext,
) -> None:
    stored = await state.get_data()
    raw_id = stored.get("watch_id")
    if not raw_id:
        await state.clear()
        raise NotFoundError("monitor not found")
    watch = await container.watches.require_watch(session, user, uuid.UUID(hex=str(raw_id)))
    await container.watches.rename(session, watch, name=message.text or "")
    await state.clear()
    await message.answer(texts.WATCH_RENAMED)
    await _render_watch(message, session, container, user, watch.id)


@router.callback_query(F.data.startswith(f"{NS}|{A_DELETE_OK}"))
async def delete_watch(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    watch = await container.watches.require_watch(session, user, data.uuid_arg(0))
    await container.confirmations.consume(
        session,
        user=user,
        action=ConfirmationAction.DELETE_WATCH,
        nonce=data.arg(1),
        context_parts=(watch.id.hex, watch.name),
    )
    await container.watches.delete(session, watch)
    await render(query, texts.WATCH_DELETED)
    await _render_watches(query, session, container, user, page=0)


@router.callback_query(F.data.startswith(f"{NS}|{A_DELETE}"))
async def confirm_delete(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    watch = await container.watches.require_watch(session, user, data.uuid_arg(0))
    confirmation = await container.confirmations.issue(
        session,
        user=user,
        action=ConfirmationAction.DELETE_WATCH,
        target_type="booking_watch",
        target_id=watch.id.hex,
        context_parts=(watch.id.hex, watch.name),
    )
    await render(
        query,
        texts.WATCH_DELETE_WARNING.format(name=watch.name),
        build_actions(
            [
                [
                    Entry(
                        "🗑 Conferma eliminazione",
                        callbacks.build(NS, A_DELETE_OK, watch.id, confirmation.nonce),
                    )
                ]
            ],
            back_target=callbacks.build(NS, A_OPEN, watch.id),
            show_cancel=True,
        ),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_EDIT}"))
async def edit_filters(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    """Open the filter editor on a draft pre-filled from the monitor."""
    await acknowledge(query)
    data = callbacks.parse(query.data)
    watch = await container.watches.require_watch(session, user, data.uuid_arg(0))
    drafts = BookingDraftRepository(session)
    await drafts.abandon_open_for_user(user.id)
    draft = await drafts.create(
        user_id=user.id, ttl_seconds=container.settings.booking_draft_ttl_seconds
    )
    copy_watch_into_draft(watch, draft)
    locations = await WatchRepository(session).locations_for(watch.id)
    draft.selected_locations_json = [
        {"id": item.portal_location_id, "name": item.location_name} for item in locations
    ]
    draft.available_locations_json = list(draft.selected_locations_json)
    await drafts.flush()
    await render(
        query,
        texts.WATCH_EDIT_TITLE,
        build_actions(
            [
                [
                    Entry(
                        "🛠 Apri i filtri",
                        callbacks.build(callbacks.BOOKING, "flt", draft.id),
                    )
                ]
            ],
            back_target=callbacks.build(NS, A_OPEN, watch.id),
            show_cancel=True,
        ),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_INTERVAL_SET}"))
async def change_interval(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    """Change how often the monitor runs, within the configured bounds."""
    await acknowledge(query)
    data = callbacks.parse(query.data)
    watch = await container.watches.require_watch(session, user, data.uuid_arg(0))
    choice = data.arg(1)
    if choice.isdigit():
        await container.watches.set_interval(session, watch, seconds=int(choice))
        await _render_watch(
            query, session, container, user, watch.id, notice="Frequenza aggiornata."
        )
        return
    minimum = container.settings.monitor_min_interval_seconds
    rows = [
        [
            Entry(
                ("✅ " if watch.interval_seconds == seconds else "")
                + (f"{seconds // 60} min" if seconds >= 60 else f"{seconds} s"),
                callbacks.build(NS, A_INTERVAL_SET, watch.id, seconds),
            )
            for seconds in group
            if seconds >= minimum
        ]
        for group in (INTERVAL_CHOICES[:2], INTERVAL_CHOICES[2:4], INTERVAL_CHOICES[4:])
    ]
    await render(
        query,
        texts.WATCH_ASK_INTERVAL.format(minimum=minimum),
        build_actions(
            [row for row in rows if row], back_target=callbacks.build(NS, A_OPEN, watch.id)
        ),
    )
