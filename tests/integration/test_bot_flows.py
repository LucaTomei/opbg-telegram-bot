"""End-to-end Telegram flows: accounts, booking, monitors and appointments."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import callbacks, texts
from app.db.models.accounts import Patient, PortalAccount
from app.db.models.users import TelegramUser
from app.db.repositories.base import utc_now
from app.db.repositories.conversations import BookingDraftRepository
from app.db.repositories.users import UserRepository
from app.db.repositories.watches import WatchRepository
from app.db.session import Database
from app.domain.enums import (
    WatchMode,
    WatchStatus,
)
from tests.support.bot import BotHarness
from tests.support.factories import make_account, make_patient, make_watch
from tests.support.portal import FakePortal

USER_ID = 710001


@pytest.fixture
async def member(clean_session: AsyncSession, bot: BotHarness) -> TelegramUser:
    user = await UserRepository(clean_session).create(telegram_id=USER_ID)
    user.status = user.status.__class__.ACTIVE
    user.consent_version = "1.0"
    user.consented_at = utc_now()
    await clean_session.commit()
    return user


@pytest.fixture
async def linked(
    clean_session: AsyncSession, member: TelegramUser
) -> tuple[PortalAccount, Patient]:
    account = await make_account(clean_session, member)
    patient = await make_patient(clean_session, account)
    await clean_session.commit()
    return account, patient


# --- accounts ----------------------------------------------------------------
async def test_the_account_screen_starts_the_in_chat_connection(
    bot: BotHarness, member: TelegramUser
) -> None:
    await bot.send(USER_ID, texts.BTN_ACCOUNTS)
    screen = bot.find("Non hai ancora collegato")
    connect = screen.callback_for("Collega account")

    bot.reset()
    await bot.press(USER_ID, connect)
    assert bot.contains("Dai un nome a questo account")


async def test_the_account_detail_never_shows_a_secret(
    bot: BotHarness, linked: tuple[PortalAccount, Patient], member: TelegramUser
) -> None:
    account, _ = linked
    await bot.press(USER_ID, callbacks.build(callbacks.ACCOUNT, "open", account.id))
    detail = bot.last()
    assert account.label in detail.text
    assert "password-sintetica" not in detail.text
    assert "utente.sintetico@example.invalid" not in detail.text


async def test_patients_are_listed_with_masked_names(
    bot: BotHarness, linked: tuple[PortalAccount, Patient], member: TelegramUser
) -> None:
    account, _ = linked
    await bot.press(USER_ID, callbacks.build(callbacks.ACCOUNT, "pat", account.id, 0))
    screen = bot.last()
    assert "AAABBB01A01A001A" not in screen.text
    assert all("AAABBB" not in label for label in screen.buttons())


async def test_account_sync_updates_the_patients(
    bot: BotHarness, linked: tuple[PortalAccount, Patient], member: TelegramUser
) -> None:
    account, _ = linked
    await bot.press(USER_ID, callbacks.build(callbacks.ACCOUNT, "sync", account.id))
    assert bot.contains("Pazienti sincronizzati")


async def test_account_removal_requires_a_double_confirmation(
    bot: BotHarness, linked: tuple[PortalAccount, Patient], member: TelegramUser, database: Database
) -> None:
    account, _ = linked
    await bot.press(USER_ID, callbacks.build(callbacks.ACCOUNT, "remove", account.id))
    warning = bot.last()
    assert "Vuoi procedere" in warning.text
    confirm = warning.callback_for("Conferma rimozione")

    bot.reset()
    await bot.press(USER_ID, confirm)
    assert bot.contains("Account rimosso")


async def test_a_replayed_removal_callback_is_ignored(
    bot: BotHarness, linked: tuple[PortalAccount, Patient], member: TelegramUser
) -> None:
    account, _ = linked
    await bot.press(USER_ID, callbacks.build(callbacks.ACCOUNT, "remove", account.id))
    confirm = bot.last().callback_for("Conferma rimozione")
    bot.reset()
    await bot.press_twice(USER_ID, confirm)
    assert sum(1 for call in bot.sent if "Account rimosso" in call.text) == 1


async def test_a_user_cannot_open_another_users_account(
    bot: BotHarness,
    clean_session: AsyncSession,
    linked: tuple[PortalAccount, Patient],
    member: TelegramUser,
) -> None:
    account, _ = linked
    intruder = await UserRepository(clean_session).create(telegram_id=710099)
    intruder.status = intruder.status.__class__.ACTIVE
    intruder.consent_version = "1.0"
    intruder.consented_at = utc_now()
    await clean_session.commit()

    await bot.press(710099, callbacks.build(callbacks.ACCOUNT, "open", account.id))
    assert bot.contains(texts.NOT_FOUND) or bot.contains("non trovato")
    assert not bot.contains(account.label)


# --- booking -----------------------------------------------------------------
async def start_booking(bot: BotHarness, account: PortalAccount, patient: Patient) -> str:
    await bot.send(USER_ID, texts.BTN_BOOK)
    accounts_screen = bot.find("Scegli l'account")
    await bot.press(USER_ID, accounts_screen.callback_for(account.label))
    patients_screen = bot.find("Scegli il paziente")
    await bot.press(USER_ID, patients_screen.callback_for("Alfa"))
    type_screen = bot.find("prestazione o uno specifico medico")
    return type_screen.callback_for(texts.BOOK_SEARCH_SERVICE)


async def test_the_booking_wizard_reaches_the_slot_list(
    bot: BotHarness, linked: tuple[PortalAccount, Patient], member: TelegramUser
) -> None:
    account, patient = linked
    service_button = await start_booking(bot, account, patient)
    await bot.press(USER_ID, service_button)
    assert bot.contains("Scrivi il nome della prestazione")

    await bot.send(USER_ID, "visita")
    results = bot.find("Risultati per")
    await bot.press(USER_ID, results.callback_for("VISITA SINTETICA"))
    locations = bot.find("Seleziona una o più sedi")
    assert texts.BOOK_ALL_LOCATIONS in " ".join(locations.buttons())

    await bot.press(USER_ID, locations.callback_for("Continua"))
    filters = bot.find("Imposta i filtri")
    await bot.press(USER_ID, filters.callback_for(texts.BTN_SHOW_AVAILABILITY))
    slots = bot.find("proposte compatibili")
    assert slots.buttons()


async def test_a_doctor_search_keeps_the_doctor_constraint(
    bot: BotHarness, linked: tuple[PortalAccount, Patient], member: TelegramUser, database: Database
) -> None:
    account, _patient = linked
    await bot.send(USER_ID, texts.BTN_BOOK)
    await bot.press(USER_ID, bot.find("Scegli l'account").callback_for(account.label))
    await bot.press(USER_ID, bot.find("Scegli il paziente").callback_for("Alfa"))
    type_screen = bot.find("prestazione o uno specifico medico")
    await bot.press(USER_ID, type_screen.callback_for(texts.BOOK_SEARCH_DOCTOR))
    await bot.send(USER_ID, "sintetico")
    results = bot.find("Risultati per")
    await bot.press(USER_ID, results.callback_for("DOTT. SINTETICO UNO"))

    async with database.read_session() as session:
        draft = await BookingDraftRepository(session).latest_open(member.id)
        assert draft is not None
        # The draft stores what the portal expects back, not the label.
        assert draft.selected_doctor_external_id == "SINTETICO_UNO"


async def test_filters_can_be_edited_from_the_wizard(
    bot: BotHarness, linked: tuple[PortalAccount, Patient], member: TelegramUser, database: Database
) -> None:
    account, patient = linked
    service_button = await start_booking(bot, account, patient)
    await bot.press(USER_ID, service_button)
    await bot.send(USER_ID, "visita")
    await bot.press(USER_ID, bot.find("Risultati per").callback_for("VISITA SINTETICA"))
    await bot.press(USER_ID, bot.find("Seleziona una o più sedi").callback_for("Continua"))
    filters = bot.find("Imposta i filtri")

    await bot.press(USER_ID, filters.callback_for(texts.BTN_FILTER_WEEKDAYS))
    weekdays = bot.find("Giorni della settimana")
    await bot.press(USER_ID, weekdays.callback_for("Domenica"))

    async with database.read_session() as session:
        draft = await BookingDraftRepository(session).latest_open(member.id)
        assert draft is not None
        assert draft.weekdays_mask != 0b1111111


async def test_time_windows_can_be_added(
    bot: BotHarness, linked: tuple[PortalAccount, Patient], member: TelegramUser, database: Database
) -> None:
    account, patient = linked
    service_button = await start_booking(bot, account, patient)
    await bot.press(USER_ID, service_button)
    await bot.send(USER_ID, "visita")
    await bot.press(USER_ID, bot.find("Risultati per").callback_for("VISITA SINTETICA"))
    await bot.press(USER_ID, bot.find("Seleziona una o più sedi").callback_for("Continua"))
    filters = bot.find("Imposta i filtri")
    await bot.press(USER_ID, filters.callback_for(texts.BTN_FILTER_TIMES))
    windows = bot.find("Fasce orarie")
    await bot.press(USER_ID, windows.callback_for("Aggiungi fascia"))
    await bot.send(USER_ID, "08:00-12:30")

    async with database.read_session() as session:
        draft = await BookingDraftRepository(session).latest_open(member.id)
        assert draft is not None
        assert draft.time_windows_json == [{"end": "12:30", "start": "08:00"}]


async def test_an_invalid_time_window_is_rejected(
    bot: BotHarness, linked: tuple[PortalAccount, Patient], member: TelegramUser
) -> None:
    account, patient = linked
    service_button = await start_booking(bot, account, patient)
    await bot.press(USER_ID, service_button)
    await bot.send(USER_ID, "visita")
    await bot.press(USER_ID, bot.find("Risultati per").callback_for("VISITA SINTETICA"))
    await bot.press(USER_ID, bot.find("Seleziona una o più sedi").callback_for("Continua"))
    filters = bot.find("Imposta i filtri")
    await bot.press(USER_ID, filters.callback_for(texts.BTN_FILTER_TIMES))
    await bot.press(USER_ID, bot.find("Fasce orarie").callback_for("Aggiungi fascia"))
    bot.reset()
    await bot.send(USER_ID, "orario sbagliato")
    assert bot.contains("Formato non valido")


async def test_booking_requires_two_confirmations(
    bot: BotHarness,
    linked: tuple[PortalAccount, Patient],
    member: TelegramUser,
    portal: FakePortal,
    bot_portal: FakePortal,
) -> None:
    account, patient = linked
    bot_portal.appointments_fixture = "appointments_list_empty.html"
    bot_portal.appointments_after_booking = "appointments_list.html"

    service_button = await start_booking(bot, account, patient)
    await bot.press(USER_ID, service_button)
    await bot.send(USER_ID, "visita")
    await bot.press(USER_ID, bot.find("Risultati per").callback_for("VISITA SINTETICA"))
    await bot.press(USER_ID, bot.find("Seleziona una o più sedi").callback_for("Continua"))
    await bot.press(USER_ID, bot.find("Imposta i filtri").callback_for(texts.BTN_SHOW_AVAILABILITY))
    slots = bot.find("proposte compatibili")
    await bot.press(USER_ID, slots.callbacks()[0])

    summary = bot.find("Riepilogo prenotazione")
    assert bot_portal.confirmations == 0
    await bot.press(USER_ID, summary.callback_for(texts.BOOK_CONFIRM_STEP1))

    second = bot.find("Confermi la prenotazione")
    assert bot_portal.confirmations == 0
    bot.reset()
    await bot.press(USER_ID, second.callback_for(texts.BOOK_CONFIRM_STEP2))
    assert bot_portal.confirmations == 1
    assert bot.contains("Prenotazione confermata")


async def test_a_replayed_confirmation_never_books_twice(
    bot: BotHarness,
    linked: tuple[PortalAccount, Patient],
    member: TelegramUser,
    bot_portal: FakePortal,
) -> None:
    account, patient = linked
    bot_portal.appointments_fixture = "appointments_list_empty.html"
    bot_portal.appointments_after_booking = "appointments_list.html"

    service_button = await start_booking(bot, account, patient)
    await bot.press(USER_ID, service_button)
    await bot.send(USER_ID, "visita")
    await bot.press(USER_ID, bot.find("Risultati per").callback_for("VISITA SINTETICA"))
    await bot.press(USER_ID, bot.find("Seleziona una o più sedi").callback_for("Continua"))
    await bot.press(USER_ID, bot.find("Imposta i filtri").callback_for(texts.BTN_SHOW_AVAILABILITY))
    await bot.press(USER_ID, bot.find("proposte compatibili").callbacks()[0])
    await bot.press(
        USER_ID, bot.find("Riepilogo prenotazione").callback_for(texts.BOOK_CONFIRM_STEP1)
    )
    confirm = bot.find("Confermi la prenotazione").callback_for(texts.BOOK_CONFIRM_STEP2)
    bot.reset()
    await bot.press_twice(USER_ID, confirm)
    assert bot_portal.confirmations == 1


async def test_a_reused_confirmation_nonce_is_refused(
    bot: BotHarness,
    linked: tuple[PortalAccount, Patient],
    member: TelegramUser,
    bot_portal: FakePortal,
) -> None:
    account, patient = linked
    bot_portal.appointments_fixture = "appointments_list_empty.html"
    bot_portal.appointments_after_booking = "appointments_list.html"

    service_button = await start_booking(bot, account, patient)
    await bot.press(USER_ID, service_button)
    await bot.send(USER_ID, "visita")
    await bot.press(USER_ID, bot.find("Risultati per").callback_for("VISITA SINTETICA"))
    await bot.press(USER_ID, bot.find("Seleziona una o più sedi").callback_for("Continua"))
    await bot.press(USER_ID, bot.find("Imposta i filtri").callback_for(texts.BTN_SHOW_AVAILABILITY))
    await bot.press(USER_ID, bot.find("proposte compatibili").callbacks()[0])
    await bot.press(
        USER_ID, bot.find("Riepilogo prenotazione").callback_for(texts.BOOK_CONFIRM_STEP1)
    )
    confirm = bot.find("Confermi la prenotazione").callback_for(texts.BOOK_CONFIRM_STEP2)
    await bot.press(USER_ID, confirm)
    bot.reset()
    # A brand new update id, so the idempotency ledger does not short-circuit it.
    await bot.press(USER_ID, confirm)
    assert bot_portal.confirmations == 1
    assert bot.contains("già stata elaborata") or bot.contains("scaduta")


async def test_no_availability_offers_a_monitor(
    bot: BotHarness,
    linked: tuple[PortalAccount, Patient],
    member: TelegramUser,
    bot_portal: FakePortal,
) -> None:
    account, patient = linked
    bot_portal.availability_fixture = "availability_empty.json"
    service_button = await start_booking(bot, account, patient)
    await bot.press(USER_ID, service_button)
    await bot.send(USER_ID, "visita")
    await bot.press(USER_ID, bot.find("Risultati per").callback_for("VISITA SINTETICA"))
    await bot.press(USER_ID, bot.find("Seleziona una o più sedi").callback_for("Continua"))
    await bot.press(USER_ID, bot.find("Imposta i filtri").callback_for(texts.BTN_SHOW_AVAILABILITY))
    assert bot.contains("Nessuna disponibilità")
    assert texts.BTN_CREATE_WATCH in " ".join(bot.last().buttons())


async def test_a_service_without_locations_is_reported_as_not_bookable(
    bot: BotHarness,
    linked: tuple[PortalAccount, Patient],
    member: TelegramUser,
    bot_portal: FakePortal,
) -> None:
    """A service the portal does not offer online must say so.

    Asking the portal for availability without a location earns a 400, which
    would otherwise reach the user as a generic technical error.
    """
    account, patient = linked
    bot_portal.locations_fixture = "appointments_create_no_locations.html"
    service_button = await start_booking(bot, account, patient)
    await bot.press(USER_ID, service_button)
    await bot.send(USER_ID, "visita")
    await bot.press(USER_ID, bot.find("Risultati per").callback_for("VISITA SINTETICA"))

    assert bot.contains("non è prenotabile online")
    assert not bot.contains("errore tecnico")


# --- monitors ----------------------------------------------------------------
async def test_the_monitor_list_is_empty_at_first(bot: BotHarness, member: TelegramUser) -> None:
    await bot.send(USER_ID, texts.BTN_WATCHES)
    assert bot.contains("Non hai monitoraggi attivi")


async def test_a_monitor_can_be_paused_resumed_and_deleted(
    bot: BotHarness,
    clean_session: AsyncSession,
    linked: tuple[PortalAccount, Patient],
    member: TelegramUser,
    database: Database,
) -> None:
    account, patient = linked
    watch = await make_watch(clean_session, member, account, patient=patient)
    await clean_session.commit()

    await bot.press(USER_ID, callbacks.build(callbacks.WATCH, "open", watch.id))
    detail = bot.last()
    await bot.press(USER_ID, detail.callback_for("Pausa"))
    assert bot.contains("Monitoraggio in pausa")

    await bot.press(USER_ID, bot.last().callback_for("Riprendi"))
    assert bot.contains("Monitoraggio riattivato")

    await bot.press(USER_ID, bot.last().callback_for("Elimina"))
    confirm = bot.last().callback_for("Conferma eliminazione")
    await bot.press(USER_ID, confirm)
    assert bot.contains("Monitoraggio eliminato")

    async with database.read_session() as session:
        assert await WatchRepository(session).get_any(watch.id) is None


async def test_notifications_can_be_toggled_independently_of_autobook(
    bot: BotHarness,
    clean_session: AsyncSession,
    linked: tuple[PortalAccount, Patient],
    member: TelegramUser,
    database: Database,
) -> None:
    account, patient = linked
    watch = await make_watch(
        clean_session, member, account, patient=patient, mode=WatchMode.AUTOBOOK_NOTIFY
    )
    await clean_session.commit()

    await bot.press(USER_ID, callbacks.build(callbacks.WATCH, "tn", watch.id))
    assert bot.contains("Notifiche disattivate")
    async with database.read_session() as session:
        reloaded = await WatchRepository(session).get_any(watch.id)
        assert reloaded is not None
        assert reloaded.notifications_enabled is False
        assert reloaded.mode is WatchMode.AUTOBOOK_NOTIFY


async def test_enabling_autobook_requires_a_confirmation(
    bot: BotHarness,
    clean_session: AsyncSession,
    linked: tuple[PortalAccount, Patient],
    member: TelegramUser,
    database: Database,
) -> None:
    account, patient = linked
    watch = await make_watch(clean_session, member, account, patient=patient)
    await clean_session.commit()

    await bot.press(USER_ID, callbacks.build(callbacks.WATCH, "sm", watch.id, "on"))
    warning = bot.last()
    assert "Attenzione" in warning.text
    async with database.read_session() as session:
        reloaded = await WatchRepository(session).get_any(watch.id)
        assert reloaded is not None
        assert reloaded.mode is WatchMode.NOTIFY

    await bot.press(USER_ID, warning.callback_for("Confermo"))
    async with database.read_session() as session:
        reloaded = await WatchRepository(session).get_any(watch.id)
        assert reloaded is not None
        assert reloaded.mode is WatchMode.AUTOBOOK_NOTIFY


async def test_a_monitor_can_be_duplicated(
    bot: BotHarness,
    clean_session: AsyncSession,
    linked: tuple[PortalAccount, Patient],
    member: TelegramUser,
    database: Database,
) -> None:
    account, patient = linked
    watch = await make_watch(clean_session, member, account, patient=patient)
    await clean_session.commit()

    await bot.press(USER_ID, callbacks.build(callbacks.WATCH, "dup", watch.id))
    assert bot.contains("Monitoraggio duplicato")
    async with database.read_session() as session:
        page = await WatchRepository(session).list_for_user(member.id, page=0, page_size=10)
        assert page.total == 2
        statuses = {item.status for item in page.items}
        assert WatchStatus.PAUSED in statuses


async def test_run_now_is_rate_limited(
    bot: BotHarness,
    clean_session: AsyncSession,
    linked: tuple[PortalAccount, Patient],
    member: TelegramUser,
) -> None:
    account, patient = linked
    watch = await make_watch(clean_session, member, account, patient=patient)
    await clean_session.commit()

    await bot.press(USER_ID, callbacks.build(callbacks.WATCH, "run", watch.id))
    assert bot.contains("Controllo richiesto")
    bot.reset()
    await bot.press(USER_ID, callbacks.build(callbacks.WATCH, "run", watch.id))
    assert bot.contains("di recente")


async def test_a_user_cannot_open_another_users_monitor(
    bot: BotHarness,
    clean_session: AsyncSession,
    linked: tuple[PortalAccount, Patient],
    member: TelegramUser,
) -> None:
    account, patient = linked
    watch = await make_watch(clean_session, member, account, patient=patient)
    intruder = await UserRepository(clean_session).create(telegram_id=710098)
    intruder.status = intruder.status.__class__.ACTIVE
    intruder.consent_version = "1.0"
    intruder.consented_at = utc_now()
    await clean_session.commit()

    await bot.press(710098, callbacks.build(callbacks.WATCH, "open", watch.id))
    assert not bot.contains(watch.name)


# --- appointments ------------------------------------------------------------
async def test_appointments_are_listed_and_synchronised(
    bot: BotHarness, linked: tuple[PortalAccount, Patient], member: TelegramUser
) -> None:
    await bot.send(USER_ID, texts.BTN_APPOINTMENTS)
    assert bot.contains("Non ci sono appuntamenti")
    refresh = bot.last().callback_for(texts.BTN_REFRESH)
    bot.reset()
    await bot.press(USER_ID, refresh)
    assert bot.contains("Sincronizzazione completata")
    assert bot.contains("Appuntamenti")


async def test_cancellation_requires_a_double_confirmation(
    bot: BotHarness,
    linked: tuple[PortalAccount, Patient],
    member: TelegramUser,
    database: Database,
    bot_portal: FakePortal,
) -> None:
    await bot.press(USER_ID, callbacks.build(callbacks.APPOINTMENT, "sync", 0))
    listing = bot.find("Appuntamenti")
    await bot.press(USER_ID, listing.callback_for("VISITA SINTETICA"))
    detail = bot.last()
    await bot.press(USER_ID, detail.callback_for(texts.BTN_CANCEL_APPOINTMENT))
    warning = bot.last()
    assert "Conferma definitiva" in warning.text
    assert bot_portal.cancel_calls == 0

    confirm = warning.callback_for("Conferma disdetta")
    bot.reset()
    await bot.press(USER_ID, confirm)
    assert bot_portal.cancel_calls == 1
    assert bot.contains("Disdetta confermata")


async def test_a_replayed_cancellation_callback_cancels_once(
    bot: BotHarness,
    linked: tuple[PortalAccount, Patient],
    member: TelegramUser,
    bot_portal: FakePortal,
) -> None:
    await bot.press(USER_ID, callbacks.build(callbacks.APPOINTMENT, "sync", 0))
    await bot.press(USER_ID, bot.find("Appuntamenti").callback_for("VISITA SINTETICA"))
    await bot.press(USER_ID, bot.last().callback_for(texts.BTN_CANCEL_APPOINTMENT))
    confirm = bot.last().callback_for("Conferma disdetta")
    bot.reset()
    await bot.press_twice(USER_ID, confirm)
    assert bot_portal.cancel_calls == 1


async def test_the_pdf_is_delivered_as_a_document(
    bot: BotHarness, linked: tuple[PortalAccount, Patient], member: TelegramUser
) -> None:
    await bot.press(USER_ID, callbacks.build(callbacks.APPOINTMENT, "sync", 0))
    await bot.press(USER_ID, bot.find("Appuntamenti").callback_for("VISITA SINTETICA"))
    detail = bot.last()
    bot.reset()
    await bot.press(USER_ID, detail.callback_for(texts.BTN_DOWNLOAD_PDF))
    assert any(call.method == "SendDocument" for call in bot.sent)


# --- settings ----------------------------------------------------------------
async def test_settings_expose_only_non_sensitive_values(
    bot: BotHarness, member: TelegramUser
) -> None:
    await bot.send(USER_ID, texts.BTN_SETTINGS)
    screen = bot.last()
    assert "Notifiche globali" in screen.text
    assert "password" not in screen.text.lower()


async def test_notifications_can_be_disabled_globally(
    bot: BotHarness, member: TelegramUser, database: Database
) -> None:
    await bot.send(USER_ID, texts.BTN_SETTINGS)
    await bot.press(USER_ID, bot.last().callback_for("Disattiva notifiche"))
    assert bot.contains("Notifiche globali disattivate")
    async with database.read_session() as session:
        reloaded = await UserRepository(session).get_by_telegram_id(USER_ID)
        assert reloaded is not None
        assert reloaded.notifications_enabled is False


async def test_quiet_hours_can_be_configured(
    bot: BotHarness, member: TelegramUser, database: Database
) -> None:
    await bot.send(USER_ID, texts.BTN_SETTINGS)
    await bot.press(USER_ID, bot.last().callback_for("Orari silenziosi"))
    await bot.send(USER_ID, "22:00-07:00")
    assert bot.contains("Orari silenziosi aggiornati")
    async with database.read_session() as session:
        reloaded = await UserRepository(session).get_by_telegram_id(USER_ID)
        assert reloaded is not None
        assert reloaded.quiet_hours_start is not None


async def test_deleting_the_profile_requires_a_confirmation(
    bot: BotHarness, linked: tuple[PortalAccount, Patient], member: TelegramUser, database: Database
) -> None:
    await bot.send(USER_ID, texts.BTN_SETTINGS)
    await bot.press(USER_ID, bot.last().callback_for("Privacy"))
    await bot.press(USER_ID, bot.last().callback_for("Elimina tutti i miei dati"))
    warning = bot.last()
    assert "Conferma definitiva" in warning.text
    await bot.press(USER_ID, warning.callback_for("Elimina definitivamente"))
    assert bot.contains("dati sono stati eliminati")
    async with database.read_session() as session:
        reloaded = await UserRepository(session).get_by_telegram_id(USER_ID)
        assert reloaded is not None
        assert reloaded.status.value == "DELETED"


async def test_settings_can_be_exported(bot: BotHarness, member: TelegramUser) -> None:
    await bot.send(USER_ID, texts.BTN_SETTINGS)
    await bot.press(USER_ID, bot.last().callback_for("Privacy"))
    await bot.press(USER_ID, bot.last().callback_for("Esporta impostazioni"))
    export = bot.last()
    assert "timezone" in export.text
    assert "password" not in export.text.lower()


# --- monitor editing ---------------------------------------------------------
async def test_a_monitor_can_be_edited_and_saved(
    bot: BotHarness,
    clean_session: AsyncSession,
    linked: tuple[PortalAccount, Patient],
    member: TelegramUser,
    database: Database,
) -> None:
    account, patient = linked
    watch = await make_watch(clean_session, member, account, patient=patient)
    await clean_session.commit()

    await bot.press(USER_ID, callbacks.build(callbacks.WATCH, "open", watch.id))
    await bot.press(USER_ID, bot.last().callback_for("Modifica filtri"))
    await bot.press(USER_ID, bot.last().callback_for("Apri i filtri"))
    filters = bot.find("Imposta i filtri")

    await bot.press(USER_ID, filters.callback_for(texts.BTN_FILTER_WEEKDAYS))
    await bot.press(USER_ID, bot.last().callback_for("Domenica"))
    await bot.press(USER_ID, bot.last().callback_for(texts.BTN_BACK))
    save = bot.find("Imposta i filtri").callback_for(texts.BTN_SAVE_WATCH)
    bot.reset()
    await bot.press(USER_ID, save)
    assert bot.contains("Monitoraggio aggiornato")

    async with database.read_session() as session:
        reloaded = await WatchRepository(session).get_any(watch.id)
        assert reloaded is not None
        assert reloaded.weekdays_mask != 0b1111111


async def test_the_monitor_frequency_can_be_changed(
    bot: BotHarness,
    clean_session: AsyncSession,
    linked: tuple[PortalAccount, Patient],
    member: TelegramUser,
    database: Database,
) -> None:
    account, patient = linked
    watch = await make_watch(clean_session, member, account, patient=patient)
    await clean_session.commit()

    await bot.press(USER_ID, callbacks.build(callbacks.WATCH, "iset", watch.id))
    await bot.press(USER_ID, bot.last().callback_for("10 min"))
    async with database.read_session() as session:
        reloaded = await WatchRepository(session).get_any(watch.id)
        assert reloaded is not None
        assert reloaded.interval_seconds == 600


async def test_the_monitor_list_shows_the_next_check(
    bot: BotHarness,
    clean_session: AsyncSession,
    linked: tuple[PortalAccount, Patient],
    member: TelegramUser,
) -> None:
    account, patient = linked
    await make_watch(clean_session, member, account, patient=patient)
    await clean_session.commit()
    await bot.send(USER_ID, texts.BTN_WATCHES)
    listing = bot.last()
    assert "Prossima verifica" in listing.text
    assert "Ultima verifica" in listing.text


async def test_location_preference_order_can_be_changed(
    bot: BotHarness,
    linked: tuple[PortalAccount, Patient],
    member: TelegramUser,
    database: Database,
) -> None:
    account, patient = linked
    service_button = await start_booking(bot, account, patient)
    await bot.press(USER_ID, service_button)
    await bot.send(USER_ID, "visita")
    await bot.press(USER_ID, bot.find("Risultati per").callback_for("VISITA SINTETICA"))
    locations = bot.find("Seleziona una o più sedi")
    await bot.press(USER_ID, locations.callback_for("Sede Alfa"))
    await bot.press(USER_ID, bot.last().callback_for("Sede Beta"))
    await bot.press(USER_ID, bot.last().callback_for("Continua"))
    filters = bot.find("Imposta i filtri")
    await bot.press(USER_ID, filters.callback_for(texts.BTN_FILTER_LOCATION_ORDER))
    order_screen = bot.last()
    assert "1. Sede Alfa" in " ".join(order_screen.buttons())

    await bot.press(USER_ID, order_screen.callback_for("2. Sede Beta"))
    assert "1. Sede Beta" in " ".join(bot.last().buttons())

    async with database.read_session() as session:
        draft = await BookingDraftRepository(session).latest_open(member.id)
        assert draft is not None
        assert [entry["name"] for entry in draft.selected_locations_json] == [
            "Sede Beta",
            "Sede Alfa",
        ]


# --- navigation safety net ----------------------------------------------------
async def test_every_back_button_of_the_wizard_leads_somewhere(
    bot: BotHarness, linked: tuple[PortalAccount, Patient], member: TelegramUser
) -> None:
    """Walk the whole booking wizard and press every «Indietro»."""
    account, patient = linked
    service_button = await start_booking(bot, account, patient)
    await bot.press(USER_ID, service_button)
    await bot.send(USER_ID, "visita")
    await bot.press(USER_ID, bot.find("Risultati per").callback_for("VISITA SINTETICA"))
    await bot.press(USER_ID, bot.find("Seleziona una o più sedi").callback_for("Continua"))
    await bot.press(USER_ID, bot.find("Imposta i filtri").callback_for(texts.BTN_SHOW_AVAILABILITY))
    await bot.press(USER_ID, bot.find("proposte compatibili").callbacks()[0])

    # Collect every distinct back target produced along the way.
    targets = {
        call.callback_for(texts.BTN_BACK)
        for call in bot.sent
        if call.keyboard is not None and texts.BTN_BACK in " ".join(call.buttons())
    }
    assert targets, "the wizard produced no back button"

    for target in sorted(targets):
        bot.reset()
        await bot.press(USER_ID, target)
        assert bot.sent, f"back target {target} produced no screen"
        rendered = " ".join(call.text for call in bot.sent)
        assert "errore tecnico" not in rendered, f"back target {target} failed: {rendered}"
        assert "non trovato" not in rendered, f"back target {target} failed: {rendered}"


async def test_the_slot_list_can_be_paginated_and_refreshed(
    bot: BotHarness,
    linked: tuple[PortalAccount, Patient],
    member: TelegramUser,
    database: Database,
) -> None:
    account, patient = linked
    async with database.session() as session:
        stored = await UserRepository(session).get_by_telegram_id(USER_ID)
        assert stored is not None
        stored.page_size = 3

    service_button = await start_booking(bot, account, patient)
    await bot.press(USER_ID, service_button)
    await bot.send(USER_ID, "visita")
    await bot.press(USER_ID, bot.find("Risultati per").callback_for("VISITA SINTETICA"))
    await bot.press(USER_ID, bot.find("Seleziona una o più sedi").callback_for("Continua"))
    await bot.press(USER_ID, bot.find("Imposta i filtri").callback_for(texts.BTN_SHOW_AVAILABILITY))
    slots = bot.find("proposte compatibili")

    bot.reset()
    await bot.press(USER_ID, slots.callback_for(texts.BTN_REFRESH))
    assert bot.contains("proposte compatibili")
    assert "errore" not in " ".join(bot.texts()).lower()


async def test_every_screen_of_the_account_and_watch_areas_opens(
    bot: BotHarness,
    clean_session: AsyncSession,
    linked: tuple[PortalAccount, Patient],
    member: TelegramUser,
) -> None:
    """Every inline control of the account and monitor screens must respond."""
    account, patient = linked
    await make_watch(clean_session, member, account, patient=patient)
    await clean_session.commit()

    visited: set[str] = set()
    for entry_point in (texts.BTN_ACCOUNTS, texts.BTN_WATCHES, texts.BTN_SETTINGS):
        bot.reset()
        await bot.send(USER_ID, entry_point)
        screen = bot.last()
        for target in screen.callbacks():
            if target in visited or target.startswith("nv|"):
                continue
            visited.add(target)
            bot.reset()
            await bot.press(USER_ID, target)
            assert bot.sent, f"{target} produced no screen"
            rendered = " ".join(call.text for call in bot.sent)
            assert "errore tecnico" not in rendered, f"{target} failed: {rendered}"
    assert visited
