"""Object factories shared by the integration and worker tests."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.accounts import Patient, PortalAccount
from app.db.models.users import TelegramUser
from app.db.models.watches import BookingWatch
from app.db.repositories.accounts import PatientRepository, PortalAccountRepository
from app.db.repositories.base import utc_now
from app.db.repositories.watches import WatchRepository
from app.domain.enums import (
    DateMode,
    LocationMode,
    SearchType,
    SortStrategy,
    WatchMode,
    WatchStatus,
)

SYNTHETIC_EMAIL = "utente.sintetico@example.invalid"
SYNTHETIC_PASSWORD = "password-sintetica-di-test"
SYNTHETIC_TAX_CODE = "AAABBB01A01A001A"


async def make_account(
    session: AsyncSession,
    user: TelegramUser,
    *,
    email: str = SYNTHETIC_EMAIL,
    label: str = "Account di test",
) -> PortalAccount:
    account = await PortalAccountRepository(session).create(
        user_id=user.id,
        label=label,
        email=email,
        password=SYNTHETIC_PASSWORD,
        encryption_key_version=1,
    )
    account.cookie_jar = {"opbg-prenotazione": "synthetic-session"}
    await PortalAccountRepository(session).flush()
    return account


async def make_patient(
    session: AsyncSession,
    account: PortalAccount,
    *,
    tax_code: str = SYNTHETIC_TAX_CODE,
    display_name: str = "Alfa Beta",
) -> Patient:
    return await PatientRepository(session).upsert(
        account_id=account.id,
        tax_code=tax_code,
        display_name=display_name,
        relation="figlio",
        now=utc_now(),
    )


async def make_watch(
    session: AsyncSession,
    user: TelegramUser,
    account: PortalAccount,
    *,
    patient: Patient | None = None,
    mode: WatchMode = WatchMode.NOTIFY,
    status: WatchStatus = WatchStatus.ACTIVE,
    next_check_at: datetime | None = None,
    notify_only_changes: bool = True,
    notifications_enabled: bool = True,
    interval_seconds: int = 300,
    rolling_days: int = 365,
) -> BookingWatch:
    resolved_patient = patient or await make_patient(session, account)
    watch = BookingWatch(
        telegram_user_id=user.id,
        portal_account_id=account.id,
        patient_id=resolved_patient.id,
        name="Monitoraggio di test",
        search_type=SearchType.SERVICE,
        query_text="visita",
        selected_service_external_id="9001",
        selected_service_name="VISITA SINTETICA",
        location_mode=LocationMode.ALL_DYNAMIC,
        date_mode=DateMode.ROLLING,
        rolling_days=rolling_days,
        weekdays_mask=0b1111111,
        time_windows_json=[],
        excluded_dates_json=[],
        sort_strategy=SortStrategy.EARLIEST,
        max_results=20,
        mode=mode,
        notifications_enabled=notifications_enabled,
        notify_only_changes=notify_only_changes,
        status=status,
        interval_seconds=interval_seconds,
        next_check_at=next_check_at or utc_now(),
        min_lead_hours=0,
    )
    return await WatchRepository(session).add(watch)
