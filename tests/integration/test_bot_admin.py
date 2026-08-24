"""Administration screens, masking, reveals and delegated actions."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import callbacks, texts
from app.db.models.accounts import Patient, PortalAccount
from app.db.models.users import TelegramUser
from app.db.repositories.audit import AuditRepository
from app.db.repositories.base import utc_now
from app.db.repositories.bookings import AppointmentRepository
from app.db.repositories.users import InviteRepository, UserRepository
from app.db.repositories.watches import WatchRepository
from app.db.session import Database
from app.domain.enums import (
    AppointmentStatus,
    AuditAction,
    UserStatus,
    WatchStatus,
)
from tests.support.bot import BotHarness
from tests.support.factories import make_account, make_patient, make_watch
from tests.support.portal import FakePortal

ADMIN_ID = 1001
MEMBER_ID = 720001
NS = callbacks.ADMIN


@pytest.fixture
async def admin(clean_session: AsyncSession, bot: BotHarness) -> TelegramUser:
    user = await UserRepository(clean_session).upsert_admin(ADMIN_ID, timezone="Europe/Rome")
    user.status = UserStatus.ACTIVE
    user.consent_version = "1.0"
    user.consented_at = utc_now()
    await clean_session.commit()
    return user


@pytest.fixture
async def member(clean_session: AsyncSession, admin: TelegramUser) -> TelegramUser:
    user = await UserRepository(clean_session).create(telegram_id=MEMBER_ID)
    user.status = UserStatus.ACTIVE
    user.consent_version = "1.0"
    user.consented_at = utc_now()
    user.first_name = "Mario"
    user.last_name = "Rossi"
    user.username = "mario_rossi"
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


async def test_the_dashboard_lists_every_section(bot: BotHarness, admin: TelegramUser) -> None:
    await bot.send(ADMIN_ID, texts.BTN_ADMIN)
    labels = " ".join(bot.last().buttons())
    for expected in (
        "Utenti",
        "Inviti",
        "Account portale",
        "Pazienti",
        "Monitoraggi",
        "Tentativi prenotazione",
        "Appuntamenti cache",
        "Notifiche",
        "Errori e circuit breaker",
        "Code e lease",
        "Audit",
        "Stato sistema",
    ):
        assert expected in labels


async def test_users_are_listed_masked(
    bot: BotHarness, admin: TelegramUser, member: TelegramUser
) -> None:
    await bot.press(ADMIN_ID, callbacks.build(NS, "usr", 0))
    screen = bot.last()
    labels = " ".join(screen.buttons())
    assert "Mario Rossi" not in labels
    assert "M. R." in labels


async def test_the_user_detail_hides_secrets(
    bot: BotHarness, admin: TelegramUser, member: TelegramUser
) -> None:
    await bot.press(ADMIN_ID, callbacks.build(NS, "u1", member.id))
    detail = bot.last()
    assert "mario_rossi" not in detail.text
    assert texts.ADMIN_NO_SECRETS in detail.text


async def test_revealing_personal_data_requires_a_reason_and_is_audited(
    bot: BotHarness, admin: TelegramUser, member: TelegramUser, database: Database
) -> None:
    await bot.press(ADMIN_ID, callbacks.build(NS, "rev", "u", member.id))
    assert bot.contains("motivo")

    bot.reset()
    await bot.send(ADMIN_ID, "no")
    assert bot.contains("Motivo troppo corto")

    bot.reset()
    await bot.send(ADMIN_ID, "verifica richiesta dall'utente")
    confirm = bot.last().callback_for("Conferma")
    bot.reset()
    await bot.press(ADMIN_ID, confirm)
    revealed = bot.last()
    assert "mario_rossi" in revealed.text
    assert texts.ADMIN_REVEAL_NOTICE in revealed.text

    async with database.read_session() as session:
        entries = await AuditRepository(session).list_entries(
            page=0, page_size=10, action=AuditAction.REVEAL_PII
        )
        assert entries.total == 1
        assert entries.items[0].reason == "verifica richiesta dall'utente"


async def test_the_account_detail_never_reveals_credentials(
    bot: BotHarness, admin: TelegramUser, linked: tuple[PortalAccount, Patient]
) -> None:
    account, _ = linked
    await bot.press(ADMIN_ID, callbacks.build(NS, "a1", account.id))
    detail = bot.last()
    assert "password-sintetica" not in detail.text
    assert "utente.sintetico@example.invalid" not in detail.text
    assert texts.ADMIN_NO_SECRETS in detail.text


async def test_revealing_an_account_shows_only_the_email(
    bot: BotHarness, admin: TelegramUser, linked: tuple[PortalAccount, Patient]
) -> None:
    account, _ = linked
    await bot.press(ADMIN_ID, callbacks.build(NS, "rev", "a", account.id))
    await bot.send(ADMIN_ID, "verifica indirizzo account")
    confirm = bot.last().callback_for("Conferma")
    bot.reset()
    await bot.press(ADMIN_ID, confirm)
    revealed = bot.last()
    assert "utente.sintetico@example.invalid" in revealed.text
    assert "password" not in revealed.text.lower()


async def test_patients_are_listed_masked(
    bot: BotHarness, admin: TelegramUser, linked: tuple[PortalAccount, Patient]
) -> None:
    await bot.press(ADMIN_ID, callbacks.build(NS, "pat", 0))
    labels = " ".join(bot.last().buttons())
    assert "AAABBB01A01A001A" not in labels
    assert "Alfa Beta" not in labels


async def test_suspending_a_user_pauses_their_monitors(
    bot: BotHarness,
    clean_session: AsyncSession,
    admin: TelegramUser,
    member: TelegramUser,
    linked: tuple[PortalAccount, Patient],
    database: Database,
) -> None:
    account, patient = linked
    watch = await make_watch(clean_session, member, account, patient=patient)
    await clean_session.commit()

    await bot.press(ADMIN_ID, callbacks.build(NS, "ss", member.id, "S"))
    await bot.send(ADMIN_ID, "sospensione per verifica")
    confirm = bot.last().callback_for("Conferma")
    await bot.press(ADMIN_ID, confirm)

    async with database.read_session() as session:
        reloaded = await UserRepository(session).get_by_telegram_id(MEMBER_ID)
        assert reloaded is not None
        assert reloaded.status is UserStatus.SUSPENDED
        stored = await WatchRepository(session).get_any(watch.id)
        assert stored is not None
        assert stored.status is WatchStatus.PAUSED


async def test_an_administrator_cannot_block_themselves(
    bot: BotHarness, admin: TelegramUser
) -> None:
    await bot.press(ADMIN_ID, callbacks.build(NS, "ss", admin.id, "B"))
    await bot.send(ADMIN_ID, "tentativo non valido")
    confirm = bot.last().callback_for("Conferma")
    bot.reset()
    await bot.press(ADMIN_ID, confirm)
    assert bot.contains("non valido") or bot.contains("Dato non valido")


async def test_invites_can_be_created_and_revoked(
    bot: BotHarness, admin: TelegramUser, database: Database
) -> None:
    await bot.press(ADMIN_ID, callbacks.build(NS, "inv", 0))
    await bot.press(ADMIN_ID, bot.last().callback_for("Nuovo invito"))
    bot.reset()
    await bot.send(ADMIN_ID, "2")
    created = bot.last()
    assert "Codice:" in created.text

    async with database.read_session() as session:
        page = await InviteRepository(session).list_invites(page=0, page_size=10)
        assert page.total == 1
        invite_id = page.items[0].id

    bot.reset()
    await bot.press(ADMIN_ID, callbacks.build(NS, "invr", invite_id))
    assert bot.contains("Invito revocato")


async def test_the_system_status_reports_versions_and_never_secrets(
    bot: BotHarness, admin: TelegramUser
) -> None:
    await bot.press(ADMIN_ID, callbacks.build(NS, "sys", 0))
    status = bot.last()
    assert "Versione applicativa" in status.text
    assert "Schema database" in status.text
    assert texts.ADMIN_NO_SECRETS in status.text


async def test_jobs_and_leases_are_visible(bot: BotHarness, admin: TelegramUser) -> None:
    await bot.press(ADMIN_ID, callbacks.build(NS, "job", 0))
    assert "Code e lease" in bot.last().text


async def test_the_circuit_breaker_can_be_overridden_with_an_audit(
    bot: BotHarness, admin: TelegramUser, database: Database
) -> None:
    await bot.press(ADMIN_ID, callbacks.build(NS, "brk", 0))
    screen = bot.last()
    await bot.press(ADMIN_ID, screen.callback_for("Apri globale"))
    await bot.send(ADMIN_ID, "sospensione manutenzione portale")
    confirm = bot.last().callback_for("Conferma")
    await bot.press(ADMIN_ID, confirm)

    async with database.read_session() as session:
        entries = await AuditRepository(session).list_entries(
            page=0, page_size=10, action=AuditAction.BREAKER_OVERRIDE
        )
        assert entries.total == 1


async def test_admin_can_run_a_monitor_of_another_user(
    bot: BotHarness,
    clean_session: AsyncSession,
    admin: TelegramUser,
    member: TelegramUser,
    linked: tuple[PortalAccount, Patient],
    database: Database,
) -> None:
    account, patient = linked
    watch = await make_watch(clean_session, member, account, patient=patient)
    await clean_session.commit()

    await bot.press(ADMIN_ID, callbacks.build(NS, "wa", watch.id, "run"))
    await bot.send(ADMIN_ID, "richiesta esplicita dell'utente")
    confirm = bot.last().callback_for("Conferma")
    await bot.press(ADMIN_ID, confirm)

    async with database.read_session() as session:
        entries = await AuditRepository(session).list_entries(
            page=0, page_size=10, action=AuditAction.WATCH_RUN_NOW
        )
        assert entries.total == 1


async def test_delegated_cancellation_is_confirmed_and_audited(
    bot: BotHarness,
    clean_session: AsyncSession,
    admin: TelegramUser,
    member: TelegramUser,
    linked: tuple[PortalAccount, Patient],
    database: Database,
    bot_portal: FakePortal,
) -> None:
    account, patient = linked
    await bot.container.appointments.sync_account(clean_session, account)
    appointment = await AppointmentRepository(clean_session).find_by_external_id(
        account.id, "70001"
    )
    assert appointment is not None
    appointment.patient_id = patient.id
    await clean_session.commit()

    await bot.press(ADMIN_ID, callbacks.build(NS, "dlg", "cancel", appointment.id))
    assert bot.contains("motivo")
    await bot.send(ADMIN_ID, "disdetta richiesta telefonicamente")
    warning = bot.last()
    assert texts.ADMIN_DELEGATED_WARNING in warning.text
    assert bot_portal.cancel_calls == 0

    confirm = warning.callback_for("Conferma")
    bot.reset()
    await bot.press(ADMIN_ID, confirm)
    assert bot_portal.cancel_calls == 1

    async with database.read_session() as session:
        entries = await AuditRepository(session).list_entries(
            page=0, page_size=10, action=AuditAction.DELEGATED_CANCELLATION
        )
        assert entries.total == 1
        stored = await AppointmentRepository(session).get_any(appointment.id)
        assert stored is not None
        assert stored.status is AppointmentStatus.CANCELLED


async def test_delegated_booking_is_audited(
    bot: BotHarness,
    clean_session: AsyncSession,
    admin: TelegramUser,
    member: TelegramUser,
    linked: tuple[PortalAccount, Patient],
    database: Database,
    bot_portal: FakePortal,
) -> None:
    account, patient = linked
    watch = await make_watch(clean_session, member, account, patient=patient)
    await clean_session.commit()
    bot_portal.appointments_fixture = "appointments_list_empty.html"
    bot_portal.appointments_after_booking = "appointments_list.html"

    await bot.press(ADMIN_ID, callbacks.build(NS, "dlg", "book", watch.id))
    await bot.send(ADMIN_ID, "prenotazione richiesta dall'utente")
    confirm = bot.last().callback_for("Conferma")
    await bot.press(ADMIN_ID, confirm)

    async with database.read_session() as session:
        entries = await AuditRepository(session).list_entries(
            page=0, page_size=10, action=AuditAction.DELEGATED_BOOKING
        )
        assert entries.total == 1
    assert bot_portal.confirmations == 1


async def test_the_audit_screen_lists_the_recorded_actions(
    bot: BotHarness, admin: TelegramUser, member: TelegramUser
) -> None:
    await bot.press(ADMIN_ID, callbacks.build(NS, "rev", "u", member.id))
    await bot.send(ADMIN_ID, "controllo dati utente")
    await bot.press(ADMIN_ID, bot.last().callback_for("Conferma"))
    bot.reset()
    await bot.press(ADMIN_ID, callbacks.build(NS, "aud", 0))
    assert "REVEAL_PII" in bot.last().text


async def test_every_admin_list_is_paginated(
    bot: BotHarness, admin: TelegramUser, member: TelegramUser
) -> None:
    for action in ("usr", "inv", "acc", "pat", "wch", "att", "app", "ntf", "evt", "aud"):
        bot.reset()
        await bot.press(ADMIN_ID, callbacks.build(NS, action, 0))
        assert bot.sent, f"section {action} produced no screen"
        assert any("🏠" in label or "Menu" in label for label in bot.last().buttons()), (
            f"section {action} has no navigation"
        )
