"""Manual booking flow: account, patient, search, locations, filters, slots."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import callbacks, texts
from app.bot.keyboards.inline import Entry, build_actions, build_list
from app.bot.presenters.formatting import (
    escape,
    format_excluded_dates,
    format_locations,
    format_patient_full,
    format_period,
    format_search,
    format_slot_button,
    format_slot_detail,
    format_time_windows,
    format_weekdays,
)
from app.bot.routers.common import (
    acknowledge,
    notify_working,
    page_size,
    render,
    send_new,
    slot_limit,
)
from app.composition import Container
from app.db.models.conversations import BookingDraft
from app.db.models.users import TelegramUser
from app.db.repositories.accounts import PatientRepository
from app.db.repositories.base import utc_now
from app.db.repositories.conversations import BookingDraftRepository
from app.db.repositories.watches import WatchRepository
from app.domain.entities import PortalSlot, SearchSelection, TimeWindow
from app.domain.enums import (
    WEEKDAY_LABELS_IT,
    BookingDraftStatus,
    ConfirmationAction,
    DateMode,
    LocationMode,
    SearchType,
    SortStrategy,
)
from app.domain.errors import DoctorServiceRequired, NotFoundError, ValidationError
from app.domain.filters import (
    parse_excluded_dates,
    parse_time_windows,
    serialize_excluded_dates,
    serialize_time_windows,
    toggle_weekday,
    weekday_selected,
)
from app.logging import get_logger
from app.services.mapping import (
    apply_draft_to_watch,
    apply_selection_to_draft,
    draft_filter,
    draft_selection,
    move_location_up,
    selected_location_entries,
)
from app.services.slot_codec import decode_locations, decode_slots, encode_locations, encode_slots

logger = get_logger(__name__)
router = Router(name="booking")

NS = callbacks.BOOKING
FL = callbacks.FILTERS

A_START = "start"
A_ACCOUNT = "acc"
A_PATIENT = "pat"
A_TYPE = "type"
A_TYPE_MENU = "tmenu"
A_RESULTS = "res"
A_PICK = "pick"
A_LOCATIONS = "loc"
A_LOC_TOGGLE = "lt"
A_LOC_ALL = "la"
A_LOC_DONE = "ld"
A_FILTERS = "flt"
A_SLOTS = "slots"
A_SLOT_PICK = "sp"
A_CONFIRM1 = "c1"
A_CONFIRM2 = "c2"
A_TO_WATCH = "tw"
A_LOC_ORDER = "lo"
A_SAVE_WATCH = "sw"

MAX_RESULT_CACHE = 40


class BookingStates(StatesGroup):
    """Free-text steps of the booking wizard."""

    waiting_query = State()
    waiting_period = State()
    waiting_time_window = State()
    waiting_excluded_date = State()


# --- draft helpers -----------------------------------------------------------
async def _require_draft(
    session: AsyncSession, user: TelegramUser, draft_id: uuid.UUID
) -> BookingDraft:
    draft = await BookingDraftRepository(session).get_owned(draft_id, user.id)
    if draft is None:
        raise NotFoundError("booking draft expired")
    return draft


async def _touch(session: AsyncSession, container: Container, draft: BookingDraft) -> None:
    await BookingDraftRepository(session).extend(
        draft, ttl_seconds=container.settings.booking_draft_ttl_seconds
    )


# --- step 1: account ---------------------------------------------------------
async def _render_accounts(
    event: CallbackQuery | Message,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    draft: BookingDraft,
    *,
    page: int = 0,
) -> None:
    accounts = [
        account
        for account in await container.accounts.list_accounts(session, user)
        if account.is_usable
    ]
    if not accounts:
        await render(event, texts.ACCOUNTS_EMPTY, build_actions([]))
        return
    size = page_size(user)
    pages = max(1, -(-len(accounts) // size))
    current = min(max(page, 0), pages - 1)
    visible = accounts[current * size : (current + 1) * size]
    entries = [
        Entry(account.label, callbacks.build(NS, A_ACCOUNT, draft.id, account.id))
        for account in visible
    ]
    await render(
        event,
        texts.BOOK_CHOOSE_ACCOUNT,
        build_list(
            entries,
            namespace=NS,
            page_action=A_START,
            page=current,
            pages=pages,
            extra=(draft.id,),
            show_cancel=True,
        ),
    )


@router.message(F.text == texts.BTN_BOOK)
async def start_booking(
    message: Message,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    state: FSMContext,
) -> None:
    """Open a fresh draft; any previous open draft is abandoned."""
    await state.clear()
    drafts = BookingDraftRepository(session)
    await drafts.abandon_open_for_user(user.id)
    draft = await drafts.create(
        user_id=user.id, ttl_seconds=container.settings.booking_draft_ttl_seconds
    )
    await _render_accounts(message, session, container, user, draft)


@router.callback_query(F.data.startswith(f"{NS}|{A_START}"))
async def paginate_accounts(
    query: CallbackQuery,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    draft = await _require_draft(session, user, data.uuid_arg(1))
    await _render_accounts(query, session, container, user, draft, page=data.int_arg(0))


# --- step 2: patient ---------------------------------------------------------
async def _render_patients(
    event: CallbackQuery | Message,
    session: AsyncSession,
    user: TelegramUser,
    draft: BookingDraft,
    *,
    page: int = 0,
) -> None:
    if draft.portal_account_id is None:
        raise NotFoundError("account not selected")
    patients = await PatientRepository(session).list_for_account(draft.portal_account_id)
    if not patients:
        await render(
            event,
            texts.PATIENTS_EMPTY,
            build_actions(
                [], back_target=callbacks.build(NS, A_START, 0, draft.id), show_cancel=True
            ),
        )
        return
    size = page_size(user)
    pages = max(1, -(-len(patients) // size))
    current = min(max(page, 0), pages - 1)
    visible = patients[current * size : (current + 1) * size]
    entries = [
        Entry(
            format_patient_full(patient),
            callbacks.build(NS, A_PATIENT, draft.id, patient.id),
        )
        for patient in visible
    ]
    await render(
        event,
        texts.BOOK_CHOOSE_PATIENT,
        build_list(
            entries,
            namespace=NS,
            page_action=A_ACCOUNT,
            page=current,
            pages=pages,
            extra=(draft.id,),
            back_target=callbacks.build(NS, A_START, 0, draft.id),
            show_cancel=True,
        ),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_ACCOUNT}"))
async def choose_account(
    query: CallbackQuery,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
) -> None:
    """Select the account, or paginate the patient list of the current one."""
    await acknowledge(query)
    data = callbacks.parse(query.data)
    draft = await _require_draft(session, user, data.uuid_arg(0))
    second = data.arg(1)
    if second.isdigit():
        await _render_patients(query, session, user, draft, page=int(second))
        return
    account = await container.accounts.require_account(session, user, data.uuid_arg(1))
    draft.portal_account_id = account.id
    draft.patient_id = None
    await _touch(session, container, draft)
    await _render_patients(query, session, user, draft)


# --- step 3: search type -----------------------------------------------------
def _search_type_keyboard(draft: BookingDraft) -> InlineKeyboardMarkup:
    return build_actions(
        [
            [
                Entry(
                    texts.BOOK_SEARCH_SERVICE,
                    callbacks.build(NS, A_TYPE, draft.id, SearchType.SERVICE.value),
                ),
                Entry(
                    texts.BOOK_SEARCH_DOCTOR,
                    callbacks.build(NS, A_TYPE, draft.id, SearchType.DOCTOR.value),
                ),
            ]
        ],
        back_target=callbacks.build(NS, A_ACCOUNT, draft.id, 0),
        show_cancel=True,
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_PATIENT}"))
async def choose_patient(
    query: CallbackQuery,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    draft = await _require_draft(session, user, data.uuid_arg(0))
    if draft.portal_account_id is None:
        raise NotFoundError("account not selected")
    patient = await PatientRepository(session).get_owned(data.uuid_arg(1), draft.portal_account_id)
    if patient is None:
        raise NotFoundError("patient not found")
    draft.patient_id = patient.id
    await _touch(session, container, draft)
    await render(query, texts.BOOK_CHOOSE_SEARCH_TYPE, _search_type_keyboard(draft))


@router.callback_query(F.data.startswith(f"{NS}|{A_TYPE}"))
async def choose_search_type(
    query: CallbackQuery,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    state: FSMContext,
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    draft = await _require_draft(session, user, data.uuid_arg(0))
    raw = data.arg(1)
    if raw not in {SearchType.SERVICE.value, SearchType.DOCTOR.value}:
        raise ValidationError("unknown search type")
    draft.search_type = SearchType(raw)
    await _touch(session, container, draft)
    await state.set_state(BookingStates.waiting_query)
    await state.update_data(draft_id=draft.id.hex)
    prompt = (
        texts.BOOK_ASK_SERVICE_QUERY
        if draft.search_type is SearchType.SERVICE
        else texts.BOOK_ASK_DOCTOR_QUERY
    )
    await render(
        query,
        prompt,
        build_actions([], back_target=callbacks.build(NS, A_TYPE_MENU, draft.id), show_cancel=True),
    )


# --- step 4: query and results ----------------------------------------------
@router.message(BookingStates.waiting_query)
async def run_search(
    message: Message,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    state: FSMContext,
) -> None:
    """Run the autocomplete and cache the exact results server side."""
    stored = await state.get_data()
    raw_id = stored.get("draft_id")
    if not raw_id:
        await state.clear()
        raise NotFoundError("booking draft expired")
    draft = await _require_draft(session, user, uuid.UUID(hex=str(raw_id)))
    if draft.portal_account_id is None or draft.patient_id is None:
        raise NotFoundError("account or patient missing")
    account = await container.accounts.require_account(session, user, draft.portal_account_id)
    patient = await PatientRepository(session).get_owned(draft.patient_id, account.id)
    if patient is None:
        raise NotFoundError("patient not found")

    query_text = (message.text or "").strip()
    draft.query_text = query_text[:160]
    await notify_working(message)
    if draft.search_type is SearchType.DOCTOR:
        doctors = await container.search.search_doctors(session, account, patient, query=query_text)
        cache = [
            {
                "kind": "doctor",
                "id": doctor.raw_value,
                "name": doctor.description,
            }
            for doctor in doctors[:MAX_RESULT_CACHE]
        ]
    else:
        services = await container.search.search_services(
            session, account, patient, query=query_text
        )
        cache = [
            {
                "kind": "service",
                "id": service.service_id,
                "name": service.description,
                "spec": service.specification_id or "",
                "online": "1" if service.online_bookable else "0",
                "prep": service.preparation_note or "",
            }
            for service in services[:MAX_RESULT_CACHE]
        ]
    draft.slot_cache = {"results": cache}
    await _touch(session, container, draft)
    await state.clear()
    if not cache:
        await message.answer(
            texts.BOOK_NO_RESULTS.format(query=escape(query_text)),
            reply_markup=build_actions(
                [], back_target=callbacks.build(NS, A_TYPE_MENU, draft.id), show_cancel=True
            ),
            parse_mode="HTML",
        )
        return
    await _render_results(message, session, user, draft, page=0)


async def _render_results(
    event: CallbackQuery | Message,
    session: AsyncSession,
    user: TelegramUser,
    draft: BookingDraft,
    *,
    page: int,
    title: str | None = None,
) -> None:
    cache = draft.slot_cache if isinstance(draft.slot_cache, dict) else {}
    results = cache.get("results") or []
    size = page_size(user)
    pages = max(1, -(-len(results) // size))
    current = min(max(page, 0), pages - 1)
    visible = results[current * size : (current + 1) * size]
    entries = [
        Entry(
            str(item.get("name", "")),
            callbacks.build(NS, A_PICK, draft.id, current * size + index),
        )
        for index, item in enumerate(visible)
    ]
    await render(
        event,
        title or texts.BOOK_RESULTS_TITLE.format(query=escape(draft.query_text or "")),
        build_list(
            entries,
            namespace=NS,
            page_action=A_RESULTS,
            page=current,
            pages=pages,
            extra=(draft.id,),
            back_target=callbacks.build(NS, A_TYPE_MENU, draft.id),
            show_cancel=True,
        ),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_RESULTS}"))
async def paginate_results(query: CallbackQuery, session: AsyncSession, user: TelegramUser) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    draft = await _require_draft(session, user, data.uuid_arg(1))
    await _render_results(query, session, user, draft, page=data.int_arg(0))


@router.callback_query(F.data.startswith(f"{NS}|{A_PICK}"))
async def pick_result(
    query: CallbackQuery,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
) -> None:
    """Store the exact selection; the first result is never chosen silently."""
    await acknowledge(query)
    data = callbacks.parse(query.data)
    draft = await _require_draft(session, user, data.uuid_arg(0))
    cache = draft.slot_cache if isinstance(draft.slot_cache, dict) else {}
    results = cache.get("results") or []
    index = data.int_arg(1, -1)
    if not 0 <= index < len(results):
        raise NotFoundError("the selected result is no longer available")
    chosen = results[index]
    if chosen.get("kind") == "doctor_service":
        # Still a doctor search: the service only narrows it down.
        draft.selected_service_external_id = str(chosen.get("id", ""))
        draft.selected_service_name = str(chosen.get("name", ""))[:255]
    elif chosen.get("kind") == "doctor":
        apply_selection_to_draft(
            draft,
            SearchSelection(
                search_type=SearchType.DOCTOR,
                query_text=draft.query_text or "",
                doctor_id=str(chosen.get("id", "")),
                doctor_name=str(chosen.get("name", "")),
            ),
        )
    else:
        apply_selection_to_draft(
            draft,
            SearchSelection(
                search_type=SearchType.SERVICE,
                query_text=draft.query_text or "",
                service_id=str(chosen.get("id", "")),
                service_name=str(chosen.get("name", "")),
                specification_id=str(chosen.get("spec", "")) or None,
                preparation_note=str(chosen.get("prep", "")) or None,
                online_bookable=str(chosen.get("online", "1")) == "1",
            ),
        )
    draft.slot_cache = None
    await _touch(session, container, draft)
    await notify_working(query)
    await _load_locations(query, session, container, user, draft)


# --- step 5: locations -------------------------------------------------------
async def _load_locations(
    event: CallbackQuery | Message,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    draft: BookingDraft,
) -> None:
    """Read the locations the portal offers for the current selection."""
    if draft.portal_account_id is None or draft.patient_id is None:
        raise NotFoundError("account or patient missing")
    account = await container.accounts.require_account(session, user, draft.portal_account_id)
    patient = await PatientRepository(session).get_owned(draft.patient_id, account.id)
    if patient is None:
        raise NotFoundError("patient not found")
    selection = draft_selection(draft)
    try:
        async with container.search.availability_flow(session, account, patient, selection) as flow:
            availability = await container.search.fetch_availability(flow)
            locations = availability.locations or flow.locations
            draft.available_locations_json = encode_locations(locations)
            draft.slot_cache = {"slots": encode_slots(availability.slots)}
            draft.slot_cache_refreshed_at = utc_now()
    except DoctorServiceRequired as exc:
        # The portal quotes nothing for a doctor alone, and only the user can
        # say which of their services is wanted.
        draft.slot_cache = {
            "results": [
                {"kind": "doctor_service", "id": service_id, "name": description}
                for service_id, description in exc.services[:MAX_RESULT_CACHE]
            ]
        }
        await _touch(session, container, draft)
        await _render_results(
            event,
            session,
            user,
            draft,
            page=0,
            title=texts.BOOK_DOCTOR_SERVICE_TITLE.format(
                doctor=escape(draft.selected_doctor_name or "")
            ),
        )
        return
    await _touch(session, container, draft)
    await _render_locations(event, session, user, draft, page=0)


async def _render_locations(
    event: CallbackQuery | Message,
    session: AsyncSession,
    user: TelegramUser,
    draft: BookingDraft,
    *,
    page: int,
) -> None:
    available = decode_locations(draft.available_locations_json)
    selected = {str(entry.get("id")) for entry in draft.selected_locations_json}
    # While editing a monitor there is no search step to go back to.
    locations_back = (
        callbacks.build(NS, A_FILTERS, draft.id)
        if draft.origin_watch_id is not None
        else callbacks.build(NS, A_RESULTS, 0, draft.id)
    )
    if not available:
        draft.location_mode = LocationMode.ALL_DYNAMIC
        await render(
            event,
            texts.BOOK_NO_LOCATIONS,
            build_actions(
                [[Entry("➡️ Continua", callbacks.build(NS, A_FILTERS, draft.id))]],
                back_target=locations_back,
                show_cancel=True,
            ),
        )
        return
    size = page_size(user)
    pages = max(1, -(-len(available) // size))
    current = min(max(page, 0), pages - 1)
    visible = available[current * size : (current + 1) * size]
    all_selected = draft.location_mode is LocationMode.ALL_DYNAMIC
    entries = [
        Entry(
            f"{'☑️' if (all_selected or location.location_id in selected) else '⬜️'} "
            f"{location.name}" + (f" ({location.slot_count})" if location.slot_count else ""),
            callbacks.build(NS, A_LOC_TOGGLE, draft.id, location.location_id),
        )
        for location in visible
    ]
    header = [
        [
            InlineKeyboardButton(
                text=("✅ " if all_selected else "") + texts.BOOK_ALL_LOCATIONS,
                callback_data=callbacks.build(NS, A_LOC_ALL, draft.id),
            )
        ]
    ]
    footer = [
        [
            InlineKeyboardButton(
                text="➡️ Continua", callback_data=callbacks.build(NS, A_LOC_DONE, draft.id)
            )
        ]
    ]
    await render(
        event,
        texts.BOOK_CHOOSE_LOCATIONS,
        build_list(
            entries,
            namespace=NS,
            page_action=A_LOCATIONS,
            page=current,
            pages=pages,
            extra=(draft.id,),
            back_target=locations_back,
            show_cancel=True,
            header_rows=header,
            footer_rows=footer,
        ),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_LOCATIONS}"))
async def paginate_locations(
    query: CallbackQuery, session: AsyncSession, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    draft = await _require_draft(session, user, data.uuid_arg(1))
    await _render_locations(query, session, user, draft, page=data.int_arg(0))


@router.callback_query(F.data.startswith(f"{NS}|{A_LOC_ALL}"))
async def select_all_locations(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    """``Tutte le sedi`` stays dynamic: new locations are included automatically."""
    await acknowledge(query)
    data = callbacks.parse(query.data)
    draft = await _require_draft(session, user, data.uuid_arg(0))
    draft.location_mode = LocationMode.ALL_DYNAMIC
    draft.selected_locations_json = []
    await _touch(session, container, draft)
    await _render_locations(query, session, user, draft, page=0)


@router.callback_query(F.data.startswith(f"{NS}|{A_LOC_TOGGLE}"))
async def toggle_location(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    draft = await _require_draft(session, user, data.uuid_arg(0))
    location_id = data.arg(1)
    available = {
        item.location_id: item for item in decode_locations(draft.available_locations_json)
    }
    if location_id not in available:
        raise NotFoundError("location not available")
    selected = list(draft.selected_locations_json)
    if draft.location_mode is LocationMode.ALL_DYNAMIC:
        selected = []
    existing = {str(entry.get("id")) for entry in selected}
    if location_id in existing:
        selected = [entry for entry in selected if str(entry.get("id")) != location_id]
    else:
        selected.append({"id": location_id, "name": available[location_id].name})
    draft.selected_locations_json = selected
    draft.location_mode = LocationMode.SELECTED if selected else LocationMode.ALL_DYNAMIC
    await _touch(session, container, draft)
    await _render_locations(query, session, user, draft, page=0)


@router.callback_query(F.data.startswith(f"{NS}|{A_LOC_DONE}"))
async def locations_done(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    draft = await _require_draft(session, user, data.uuid_arg(0))
    if draft.location_mode is LocationMode.SELECTED and not draft.selected_locations_json:
        await acknowledge(query, texts.BOOK_LOCATIONS_NONE)
        await _render_locations(query, session, user, draft, page=0)
        return
    await _render_filters(query, session, user, draft)


# --- step 6: filters ---------------------------------------------------------
def _filters_summary(draft: BookingDraft) -> str:
    windows = parse_time_windows(draft.time_windows_json)
    excluded = sorted(parse_excluded_dates(draft.excluded_dates_json))
    names = [str(entry.get("name", "")) for entry in draft.selected_locations_json]
    return "\n".join(
        [
            texts.BOOK_FILTERS_TITLE,
            "",
            f"Ricerca: {
                format_search(
                    search_type=draft.search_type,
                    service_name=draft.selected_service_name,
                    doctor_name=draft.selected_doctor_name,
                    query=draft.query_text,
                )
            }",
            f"Sedi: {format_locations(draft.location_mode, names)}",
            f"Periodo: {
                format_period(
                    date_mode=draft.date_mode,
                    date_from=draft.date_from,
                    date_to=draft.date_to,
                    rolling_days=draft.rolling_days,
                )
            }",
            f"Giorni: {format_weekdays(draft.weekdays_mask)}",
            f"Fasce orarie: {format_time_windows(windows)}",
            f"Date escluse: {format_excluded_dates(excluded)}",
            f"Anticipo minimo: {draft.min_lead_hours} ore",
            f"Ordinamento: {texts.FILTER_SORT_LABELS.get(draft.sort_strategy.value, '')}",
        ]
    )


async def _render_filters(
    event: CallbackQuery | Message,
    session: AsyncSession,
    user: TelegramUser,
    draft: BookingDraft,
) -> None:
    """Filter editor, shared by the booking wizard and by monitor editing."""
    editing = draft.origin_watch_id is not None
    rows = [
        [
            Entry(texts.BTN_FILTER_PERIOD, callbacks.build(FL, "period", draft.id)),
            Entry(texts.BTN_FILTER_WEEKDAYS, callbacks.build(FL, "wd", draft.id)),
        ],
        [
            Entry(texts.BTN_FILTER_TIMES, callbacks.build(FL, "times", draft.id)),
            Entry(texts.BTN_FILTER_EXCLUDED, callbacks.build(FL, "exc", draft.id)),
        ],
        [
            Entry(texts.BTN_FILTER_SORT, callbacks.build(FL, "sort", draft.id)),
            Entry(texts.BTN_FILTER_LEAD, callbacks.build(FL, "lead", draft.id)),
        ],
        [
            Entry(texts.BTN_FILTER_LOCATIONS, callbacks.build(NS, A_LOCATIONS, 0, draft.id)),
            Entry(texts.BTN_FILTER_LOCATION_ORDER, callbacks.build(NS, A_LOC_ORDER, draft.id)),
        ],
    ]
    if editing:
        rows.append([Entry(texts.BTN_SAVE_WATCH, callbacks.build(NS, A_SAVE_WATCH, draft.id))])
    else:
        rows.append([Entry(texts.BTN_SHOW_AVAILABILITY, callbacks.build(NS, A_SLOTS, 0, draft.id))])
    back = (
        callbacks.build(callbacks.WATCH, "open", draft.origin_watch_id)
        if draft.origin_watch_id is not None
        else callbacks.build(NS, A_LOCATIONS, 0, draft.id)
    )
    await render(
        event,
        _filters_summary(draft),
        build_actions(rows, back_target=back, show_cancel=True),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_FILTERS}"))
async def open_filters(query: CallbackQuery, session: AsyncSession, user: TelegramUser) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    draft = await _require_draft(session, user, data.uuid_arg(0))
    await _render_filters(query, session, user, draft)


@router.callback_query(F.data.startswith(f"{FL}|period"))
async def filter_period(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    draft = await _require_draft(session, user, data.uuid_arg(0))
    choice = data.arg(1)
    if choice.isdigit():
        draft.date_mode = DateMode.ROLLING
        draft.rolling_days = int(choice)
        draft.date_from = None
        draft.date_to = None
        await _touch(session, container, draft)
        await _render_filters(query, session, user, draft)
        return
    rows = [
        [
            Entry(
                texts.FILTER_PERIOD_ROLLING.format(days=days),
                callbacks.build(FL, "period", draft.id, days),
            )
            for days in group
        ]
        for group in ((7, 15), (30, 60), (90, 180))
    ]
    rows.append([Entry(texts.FILTER_PERIOD_FIXED, callbacks.build(FL, "fixed", draft.id))])
    await render(
        query,
        texts.FILTER_PERIOD_TITLE,
        build_actions(rows, back_target=callbacks.build(NS, A_FILTERS, draft.id)),
    )


@router.callback_query(F.data.startswith(f"{FL}|fixed"))
async def ask_fixed_period(
    query: CallbackQuery, session: AsyncSession, user: TelegramUser, state: FSMContext
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    draft = await _require_draft(session, user, data.uuid_arg(0))
    await state.set_state(BookingStates.waiting_period)
    await state.update_data(draft_id=draft.id.hex)
    await render(
        query,
        texts.FILTER_PERIOD_ASK_RANGE,
        build_actions([], back_target=callbacks.build(FL, "period", draft.id), show_cancel=True),
    )


def _parse_range(raw: str) -> tuple[date, date]:
    parts = [chunk.strip() for chunk in raw.replace("—", "-").split("-")]
    if len(parts) != 2:
        raise ValidationError("invalid period format")
    try:
        start = datetime.strptime(parts[0], "%d/%m/%Y").date()
        end = datetime.strptime(parts[1], "%d/%m/%Y").date()
    except ValueError as exc:
        raise ValidationError("invalid period format") from exc
    if end < start:
        raise ValidationError("the end date precedes the start date")
    return start, end


@router.message(BookingStates.waiting_period)
async def apply_fixed_period(
    message: Message,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    state: FSMContext,
) -> None:
    stored = await state.get_data()
    draft = await _require_draft(session, user, uuid.UUID(hex=str(stored.get("draft_id"))))
    try:
        start, end = _parse_range(message.text or "")
    except ValidationError:
        await message.answer(texts.FILTER_PERIOD_INVALID, parse_mode="HTML")
        return
    draft.date_mode = DateMode.FIXED
    draft.date_from = start
    draft.date_to = end
    draft.rolling_days = None
    await _touch(session, container, draft)
    await state.clear()
    await _render_filters(message, session, user, draft)


@router.callback_query(F.data.startswith(f"{FL}|wd"))
async def filter_weekdays(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    draft = await _require_draft(session, user, data.uuid_arg(0))
    index = data.arg(1)
    if index.isdigit():
        draft.weekdays_mask = toggle_weekday(draft.weekdays_mask, int(index))
        await _touch(session, container, draft)
    rows = [
        [
            Entry(
                f"{'☑️' if weekday_selected(draft.weekdays_mask, day) else '⬜️'} "
                f"{WEEKDAY_LABELS_IT[day]}",
                callbacks.build(FL, "wd", draft.id, day),
            )
            for day in group
        ]
        for group in ((0, 1), (2, 3), (4, 5), (6,))
    ]
    await render(
        query,
        texts.FILTER_WEEKDAYS_TITLE,
        build_actions(rows, back_target=callbacks.build(NS, A_FILTERS, draft.id)),
    )


@router.callback_query(F.data.startswith(f"{FL}|times"))
async def filter_times(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    draft = await _require_draft(session, user, data.uuid_arg(0))
    action = data.arg(1)
    if action == "clear":
        draft.time_windows_json = []
        await _touch(session, container, draft)
    elif action.isdigit():
        stored = list(parse_time_windows(draft.time_windows_json))
        index = int(action)
        if 0 <= index < len(stored):
            stored.pop(index)
            draft.time_windows_json = serialize_time_windows(stored)
            await _touch(session, container, draft)
    windows = parse_time_windows(draft.time_windows_json)
    rows = [
        [
            Entry(
                f"🗑 {window.start.strftime('%H:%M')}–{window.end.strftime('%H:%M')}",
                callbacks.build(FL, "times", draft.id, index),
            )
        ]
        for index, window in enumerate(windows)
    ]
    rows.append([Entry(texts.FILTER_TIMES_ADD, callbacks.build(FL, "addtime", draft.id))])
    rows.append([Entry(texts.FILTER_TIMES_ANY, callbacks.build(FL, "times", draft.id, "clear"))])
    await render(
        query,
        texts.FILTER_TIMES_TITLE.format(current=format_time_windows(windows)),
        build_actions(rows, back_target=callbacks.build(NS, A_FILTERS, draft.id)),
    )


@router.callback_query(F.data.startswith(f"{FL}|addtime"))
async def ask_time_window(
    query: CallbackQuery, session: AsyncSession, user: TelegramUser, state: FSMContext
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    draft = await _require_draft(session, user, data.uuid_arg(0))
    await state.set_state(BookingStates.waiting_time_window)
    await state.update_data(draft_id=draft.id.hex)
    await render(
        query,
        texts.FILTER_TIMES_ASK,
        build_actions([], back_target=callbacks.build(FL, "times", draft.id), show_cancel=True),
    )


@router.message(BookingStates.waiting_time_window)
async def apply_time_window(
    message: Message,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    state: FSMContext,
) -> None:
    stored = await state.get_data()
    draft = await _require_draft(session, user, uuid.UUID(hex=str(stored.get("draft_id"))))
    raw = (message.text or "").replace(" ", "")
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
    windows = [*parse_time_windows(draft.time_windows_json), TimeWindow(start=start, end=end)]
    draft.time_windows_json = serialize_time_windows(windows)
    await _touch(session, container, draft)
    await state.clear()
    await _render_filters(message, session, user, draft)


@router.callback_query(F.data.startswith(f"{FL}|exc"))
async def filter_excluded(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    draft = await _require_draft(session, user, data.uuid_arg(0))
    target = data.arg(1)
    if target:
        remaining = [value for value in draft.excluded_dates_json if str(value) != target]
        draft.excluded_dates_json = remaining
        await _touch(session, container, draft)
    excluded = sorted(parse_excluded_dates(draft.excluded_dates_json))
    rows = [
        [
            Entry(
                f"🗑 {value.strftime('%d/%m/%Y')}",
                callbacks.build(FL, "exc", draft.id, value.isoformat()),
            )
        ]
        for value in excluded
    ]
    rows.append([Entry(texts.FILTER_EXCLUDED_ADD, callbacks.build(FL, "addexc", draft.id))])
    await render(
        query,
        texts.FILTER_EXCLUDED_TITLE.format(current=format_excluded_dates(excluded)),
        build_actions(rows, back_target=callbacks.build(NS, A_FILTERS, draft.id)),
    )


@router.callback_query(F.data.startswith(f"{FL}|addexc"))
async def ask_excluded_date(
    query: CallbackQuery, session: AsyncSession, user: TelegramUser, state: FSMContext
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    draft = await _require_draft(session, user, data.uuid_arg(0))
    await state.set_state(BookingStates.waiting_excluded_date)
    await state.update_data(draft_id=draft.id.hex)
    await render(
        query,
        texts.FILTER_EXCLUDED_ASK,
        build_actions([], back_target=callbacks.build(FL, "exc", draft.id), show_cancel=True),
    )


@router.message(BookingStates.waiting_excluded_date)
async def apply_excluded_date(
    message: Message,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    state: FSMContext,
) -> None:
    stored = await state.get_data()
    draft = await _require_draft(session, user, uuid.UUID(hex=str(stored.get("draft_id"))))
    try:
        value = datetime.strptime((message.text or "").strip(), "%d/%m/%Y").date()
    except ValueError:
        await message.answer(texts.FILTER_EXCLUDED_INVALID, parse_mode="HTML")
        return
    current = set(parse_excluded_dates(draft.excluded_dates_json))
    current.add(value)
    draft.excluded_dates_json = serialize_excluded_dates(current)
    await _touch(session, container, draft)
    await state.clear()
    await _render_filters(message, session, user, draft)


@router.callback_query(F.data.startswith(f"{FL}|sort"))
async def filter_sort(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    draft = await _require_draft(session, user, data.uuid_arg(0))
    choice = data.arg(1)
    if choice in {item.value for item in SortStrategy}:
        draft.sort_strategy = SortStrategy(choice)
        await _touch(session, container, draft)
        await _render_filters(query, session, user, draft)
        return
    rows = [
        [
            Entry(
                ("✅ " if draft.sort_strategy is strategy else "")
                + texts.FILTER_SORT_LABELS[strategy.value],
                callbacks.build(FL, "sort", draft.id, strategy.value),
            )
        ]
        for strategy in SortStrategy
    ]
    await render(
        query,
        texts.FILTER_SORT_TITLE,
        build_actions(rows, back_target=callbacks.build(NS, A_FILTERS, draft.id)),
    )


@router.callback_query(F.data.startswith(f"{FL}|lead"))
async def filter_lead(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    await acknowledge(query)
    data = callbacks.parse(query.data)
    draft = await _require_draft(session, user, data.uuid_arg(0))
    choice = data.arg(1)
    if choice.isdigit():
        draft.min_lead_hours = int(choice)
        await _touch(session, container, draft)
        await _render_filters(query, session, user, draft)
        return
    rows = [
        [
            Entry(
                ("✅ " if draft.min_lead_hours == hours else "")
                + (f"{hours} ore" if hours else "nessun anticipo"),
                callbacks.build(FL, "lead", draft.id, hours),
            )
            for hours in group
        ]
        for group in ((0, 6), (12, 24), (48, 72))
    ]
    await render(
        query,
        texts.FILTER_LEAD_TITLE,
        build_actions(rows, back_target=callbacks.build(NS, A_FILTERS, draft.id)),
    )


# --- step 7: slots -----------------------------------------------------------
async def _refresh_slots(
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    draft: BookingDraft,
) -> list[PortalSlot]:
    """Re-read availability from the portal and cache it inside the draft."""
    if draft.portal_account_id is None or draft.patient_id is None:
        raise NotFoundError("account or patient missing")
    account = await container.accounts.require_account(session, user, draft.portal_account_id)
    patient = await PatientRepository(session).get_owned(draft.patient_id, account.id)
    if patient is None:
        raise NotFoundError("patient not found")
    selection = draft_selection(draft)
    slot_filter = draft_filter(draft, max_results=slot_limit(user))
    result = await container.search.collect(
        session, account, patient, selection, slot_filter, now=utc_now()
    )
    draft.available_locations_json = encode_locations(result.locations)
    draft.slot_cache = {"slots": encode_slots(result.slots)}
    draft.slot_cache_refreshed_at = utc_now()
    await _touch(session, container, draft)
    return list(result.slots)


def _cached_slots(draft: BookingDraft) -> list[PortalSlot]:
    cache = draft.slot_cache if isinstance(draft.slot_cache, dict) else {}
    return list(decode_slots(cache.get("slots")))


async def _render_slots(
    event: CallbackQuery | Message,
    session: AsyncSession,
    user: TelegramUser,
    draft: BookingDraft,
    *,
    page: int,
) -> None:
    slots = _cached_slots(draft)
    if not slots:
        await render(
            event,
            texts.BOOK_NO_SLOTS,
            build_actions(
                [
                    [
                        Entry(
                            texts.BTN_REFRESH,
                            callbacks.build(NS, A_SLOTS, 0, draft.id, "r"),
                        ),
                        Entry(texts.BTN_CREATE_WATCH, callbacks.build(NS, A_TO_WATCH, draft.id)),
                    ]
                ],
                back_target=callbacks.build(NS, A_FILTERS, draft.id),
                show_cancel=True,
            ),
        )
        return
    size = page_size(user)
    pages = max(1, -(-len(slots) // size))
    current = min(max(page, 0), pages - 1)
    visible = slots[current * size : (current + 1) * size]
    entries = [
        Entry(
            format_slot_button(slot),
            callbacks.build(NS, A_SLOT_PICK, draft.id, current * size + index),
        )
        for index, slot in enumerate(visible)
    ]
    footer = [
        [
            InlineKeyboardButton(
                text=texts.BTN_REFRESH,
                callback_data=callbacks.build(NS, A_SLOTS, current, draft.id, "r"),
            ),
            InlineKeyboardButton(
                text=texts.BTN_CREATE_WATCH,
                callback_data=callbacks.build(NS, A_TO_WATCH, draft.id),
            ),
        ]
    ]
    detail = "\n\n".join(format_slot_detail(slot) for slot in visible[:3])
    await render(
        event,
        f"{texts.BOOK_SLOTS_TITLE.format(count=len(slots))}\n\n{detail}",
        build_list(
            entries,
            namespace=NS,
            page_action=A_SLOTS,
            page=current,
            pages=pages,
            extra=(draft.id,),
            back_target=callbacks.build(NS, A_FILTERS, draft.id),
            show_cancel=True,
            footer_rows=footer,
        ),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_SLOTS}"))
async def show_slots(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    """Show the paginated slots, refreshing from the portal when requested."""
    await acknowledge(query)
    data = callbacks.parse(query.data)
    page = data.int_arg(0)
    draft = await _require_draft(session, user, data.uuid_arg(1))
    refresh = data.arg(2) == "r"
    if refresh or draft.slot_cache_refreshed_at is None:
        await notify_working(query)
        await _refresh_slots(session, container, user, draft)
        page = 0
    else:
        slot_filter = draft_filter(draft, max_results=slot_limit(user))
        from app.domain.filters import apply_filter

        cached = _cached_slots(draft)
        filtered = apply_filter(cached, slot_filter, now=utc_now())
        draft.slot_cache = {"slots": encode_slots(filtered)}
    await _render_slots(query, session, user, draft, page=page)


# --- step 8: confirmation ----------------------------------------------------
async def _summary_text(
    session: AsyncSession, container: Container, user: TelegramUser, draft: BookingDraft
) -> str:
    if draft.portal_account_id is None or draft.patient_id is None:
        raise NotFoundError("account or patient missing")
    account = await container.accounts.require_account(session, user, draft.portal_account_id)
    patient = await PatientRepository(session).get_owned(draft.patient_id, account.id)
    if patient is None:
        raise NotFoundError("patient not found")
    slot_payload = draft.selected_slot
    if not isinstance(slot_payload, dict):
        raise NotFoundError("no slot selected")
    from app.services.slot_codec import slot_from_dict

    slot = slot_from_dict(slot_payload)
    return "\n".join(
        [
            texts.BOOK_SUMMARY_TITLE,
            "",
            f"Account: <b>{escape(account.label)}</b>",
            f"Paziente: <b>{format_patient_full(patient)}</b>",
            f"Ricerca: {
                format_search(
                    search_type=draft.search_type,
                    service_name=draft.selected_service_name,
                    doctor_name=draft.selected_doctor_name,
                    query=draft.query_text,
                )
            }",
            "",
            format_slot_detail(slot),
        ]
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_SLOT_PICK}"))
async def pick_slot(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    """First confirmation step: show the full summary."""
    await acknowledge(query)
    data = callbacks.parse(query.data)
    draft = await _require_draft(session, user, data.uuid_arg(0))
    slots = _cached_slots(draft)
    index = data.int_arg(1, -1)
    if not 0 <= index < len(slots):
        raise NotFoundError("the selected slot is no longer available")
    from app.services.slot_codec import slot_to_dict

    draft.selected_slot = slot_to_dict(slots[index])
    await _touch(session, container, draft)
    summary = await _summary_text(session, container, user, draft)
    await render(
        query,
        summary,
        build_actions(
            [[Entry(texts.BOOK_CONFIRM_STEP1, callbacks.build(NS, A_CONFIRM1, draft.id))]],
            back_target=callbacks.build(NS, A_SLOTS, 0, draft.id),
            show_cancel=True,
        ),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_CONFIRM1}"))
async def confirm_step_one(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    """Second confirmation step: issue a fresh, expiring nonce."""
    await acknowledge(query)
    data = callbacks.parse(query.data)
    draft = await _require_draft(session, user, data.uuid_arg(0))
    slot_payload = draft.selected_slot
    if not isinstance(slot_payload, dict):
        raise NotFoundError("no slot selected")
    confirmation = await container.confirmations.issue(
        session,
        user=user,
        action=ConfirmationAction.BOOK_SLOT,
        target_type="booking_draft",
        target_id=draft.id.hex,
        context_parts=(
            draft.id.hex,
            str(slot_payload.get("proposal_code", "")),
            str(slot_payload.get("starts_at_local", "")),
            str(slot_payload.get("location_id", "")),
        ),
    )
    summary = await _summary_text(session, container, user, draft)
    prompt = texts.BOOK_CONFIRM_PROMPT.format(seconds=confirmation.expires_in_seconds)
    await render(
        query,
        f"{summary}\n\n{prompt}",
        build_actions(
            [
                [
                    Entry(
                        texts.BOOK_CONFIRM_STEP2,
                        callbacks.build(NS, A_CONFIRM2, draft.id, confirmation.nonce),
                    )
                ]
            ],
            back_target=callbacks.build(NS, A_SLOTS, 0, draft.id),
            show_cancel=True,
        ),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_CONFIRM2}"))
async def confirm_booking(
    query: CallbackQuery,
    session: AsyncSession,
    container: Container,
    user: TelegramUser,
    correlation_id: str,
) -> None:
    """Consume the nonce and run the idempotent booking protocol once."""
    await acknowledge(query)
    data = callbacks.parse(query.data)
    draft = await _require_draft(session, user, data.uuid_arg(0))
    slot_payload = draft.selected_slot
    if not isinstance(slot_payload, dict):
        raise NotFoundError("no slot selected")
    await container.confirmations.consume(
        session,
        user=user,
        action=ConfirmationAction.BOOK_SLOT,
        nonce=data.arg(1),
        context_parts=(
            draft.id.hex,
            str(slot_payload.get("proposal_code", "")),
            str(slot_payload.get("starts_at_local", "")),
            str(slot_payload.get("location_id", "")),
        ),
    )
    if draft.portal_account_id is None or draft.patient_id is None:
        raise NotFoundError("account or patient missing")
    account = await container.accounts.require_account(session, user, draft.portal_account_id)
    patient = await PatientRepository(session).get_owned(draft.patient_id, account.id)
    if patient is None:
        raise NotFoundError("patient not found")

    from app.services.bookings import BookingRequest
    from app.services.slot_codec import slot_from_dict

    request = BookingRequest(
        account=account,
        patient=patient,
        selection=draft_selection(draft),
        slot_filter=draft_filter(draft, max_results=slot_limit(user)),
        slot=slot_from_dict(slot_payload),
        user_id=user.id,
        draft_id=draft.id,
    )
    result = await container.bookings.book(session, request)

    if result.outcome.succeeded:
        from app.domain.enums import BookingDraftStatus

        await BookingDraftRepository(session).close(draft, BookingDraftStatus.CONFIRMED)
        details = await _success_details(session, container, user, result)
        await render(
            query,
            texts.BOOK_SUCCESS.format(details=details),
            build_actions(
                [
                    [
                        Entry(
                            texts.BTN_DOWNLOAD_PDF,
                            callbacks.build(
                                callbacks.APPOINTMENT,
                                "pdf",
                                await _appointment_uuid(session, container, user, result),
                            ),
                        )
                    ],
                    [
                        Entry(
                            texts.BTN_MY_APPOINTMENTS,
                            callbacks.build(callbacks.APPOINTMENT, "list", 0),
                        )
                    ],
                ]
            ),
        )
        return
    if result.outcome.ambiguous:
        await render(
            query,
            texts.BOOK_AMBIGUOUS.format(cid=correlation_id),
            build_actions(
                [
                    [
                        Entry(
                            texts.BTN_MY_APPOINTMENTS,
                            callbacks.build(callbacks.APPOINTMENT, "list", 0),
                        )
                    ]
                ]
            ),
        )
        return
    await _refresh_slots(session, container, user, draft)
    await render(query, texts.BOOK_SLOT_LOST)
    await _render_slots(query, session, user, draft, page=0)


async def _appointment_uuid(
    session: AsyncSession, container: Container, user: TelegramUser, result: object
) -> uuid.UUID:
    """Resolve the local appointment created by a successful booking."""
    from app.db.repositories.bookings import AppointmentRepository

    attempt = getattr(result, "attempt", None)
    external_id = getattr(getattr(result, "outcome", None), "appointment_external_id", None)
    if attempt is None or not external_id:
        raise NotFoundError("appointment not found")
    record = await AppointmentRepository(session).find_by_external_id(
        attempt.portal_account_id, str(external_id)
    )
    if record is None:
        raise NotFoundError("appointment not found")
    return record.id


async def _success_details(
    session: AsyncSession, container: Container, user: TelegramUser, result: object
) -> str:
    from app.bot.presenters.formatting import format_appointment_card
    from app.db.repositories.bookings import AppointmentRepository

    attempt = getattr(result, "attempt", None)
    external_id = getattr(getattr(result, "outcome", None), "appointment_external_id", None)
    if attempt is None or not external_id:
        return "Prenotazione registrata."
    record = await AppointmentRepository(session).find_by_external_id(
        attempt.portal_account_id, str(external_id)
    )
    if record is None:
        return "Prenotazione registrata."
    return format_appointment_card(record)


# --- location preference order -----------------------------------------------
@router.callback_query(F.data.startswith(f"{NS}|{A_LOC_ORDER}"))
async def order_locations(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    """Reorder the selected locations without excluding any of them."""
    await acknowledge(query)
    data = callbacks.parse(query.data)
    draft = await _require_draft(session, user, data.uuid_arg(0))
    target = data.arg(1)
    if target:
        draft.selected_locations_json = move_location_up(
            list(draft.selected_locations_json), target
        )
        await _touch(session, container, draft)
    entries = list(draft.selected_locations_json)
    if draft.location_mode is LocationMode.ALL_DYNAMIC or not entries:
        await render(
            query,
            texts.FILTER_ORDER_UNAVAILABLE,
            build_actions([], back_target=callbacks.build(NS, A_FILTERS, draft.id)),
        )
        return
    rows = [
        [
            Entry(
                f"{position + 1}. {entry.get('name', '')}" + ("" if position == 0 else "  ⬆️"),
                callbacks.build(NS, A_LOC_ORDER, draft.id, str(entry.get("id", ""))),
            )
        ]
        for position, entry in enumerate(entries)
    ]
    await render(
        query,
        texts.FILTER_ORDER_TITLE,
        build_actions(rows, back_target=callbacks.build(NS, A_FILTERS, draft.id)),
    )


# --- saving an edited monitor ------------------------------------------------
@router.callback_query(F.data.startswith(f"{NS}|{A_SAVE_WATCH}"))
async def save_watch_edits(
    query: CallbackQuery, session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    """Write the edited filters back onto the monitor and close the draft."""
    await acknowledge(query)
    data = callbacks.parse(query.data)
    draft = await _require_draft(session, user, data.uuid_arg(0))
    if draft.origin_watch_id is None:
        raise NotFoundError("this draft does not edit a monitor")
    watch = await container.watches.require_watch(session, user, draft.origin_watch_id)
    apply_draft_to_watch(draft, watch)
    watches = WatchRepository(session)
    if draft.location_mode is LocationMode.ALL_DYNAMIC:
        await watches.set_locations(watch, [])
    else:
        await watches.set_locations(watch, selected_location_entries(draft))
    watch.missing_location_reported_at = None
    await watches.flush()
    await BookingDraftRepository(session).close(draft, BookingDraftStatus.CONFIRMED)
    await render(query, texts.WATCH_UPDATED)
    await send_new(
        query,
        texts.WATCH_UPDATED_HINT,
        build_actions(
            [
                [
                    Entry(
                        "🔍 Apri il monitoraggio",
                        callbacks.build(callbacks.WATCH, "open", watch.id),
                    )
                ]
            ]
        ),
    )


@router.callback_query(F.data.startswith(f"{NS}|{A_TYPE_MENU}"))
async def back_to_search_type(
    query: CallbackQuery, session: AsyncSession, user: TelegramUser
) -> None:
    """Return to the «prestazione o medico» screen from any later step."""
    await acknowledge(query)
    data = callbacks.parse(query.data)
    draft = await _require_draft(session, user, data.uuid_arg(0))
    await render(query, texts.BOOK_CHOOSE_SEARCH_TYPE, _search_type_keyboard(draft))
