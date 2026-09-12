"""Formatting helpers shared by every screen.

Everything a user sees is masked where required and escaped for Telegram HTML.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from datetime import date, datetime, time

from app.bot import texts
from app.db.models.accounts import Patient, PortalAccount
from app.db.models.bookings import AppointmentCache
from app.db.models.watches import BookingWatch
from app.domain.entities import PortalSlot, TimeWindow
from app.domain.enums import (
    ALL_WEEKDAYS_MASK,
    DateMode,
    LocationMode,
    SearchType,
)
from app.domain.filters import ROME, to_local
from app.security.redaction import mask_person_name

WEEKDAY_SHORT: tuple[str, ...] = ("Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom")
MONTHS_IT: tuple[str, ...] = (
    "gennaio",
    "febbraio",
    "marzo",
    "aprile",
    "maggio",
    "giugno",
    "luglio",
    "agosto",
    "settembre",
    "ottobre",
    "novembre",
    "dicembre",
)


def checkbox(selected: bool) -> str:
    """State marker for a row the user can switch on and off.

    One helper for every multiple-choice list, so a selected row never reads
    differently from one screen to the next.  ``✅`` and not ``☑️``: the latter
    draws as a pale grey box on iOS, which next to a green tick reads as the
    off state rather than the on one.
    """
    return "✅" if selected else "⬜️"


def escape(value: str | None) -> str:
    return html.escape(value or "", quote=False)


def format_date(value: date) -> str:
    return f"{value.day} {MONTHS_IT[value.month - 1]} {value.year}"


def format_short_date(value: date) -> str:
    return value.strftime("%d/%m/%Y")


def format_time(value: time) -> str:
    return value.strftime("%H:%M")


def format_local_datetime(value: datetime | None) -> str:
    """Render a stored UTC instant in Europe/Rome local time."""
    if value is None:
        return "data non disponibile"
    local = to_local(value, ROME)
    weekday = WEEKDAY_SHORT[local.weekday()]
    return f"{weekday} {local.strftime('%d/%m/%Y')} alle {local.strftime('%H:%M')}"


def format_slot_button(slot: PortalSlot) -> str:
    """Label one slot: when, where and what it costs.

    The price belongs on the button. Reading it only in the detail above means
    comparing proposals by scrolling, and it is what people decide on.
    """
    local = slot.starts_at_local
    parts = [local.strftime("%d/%m %H:%M")]
    price = format_price(slot.cost)
    if price:
        parts.append(price)
    parts.append(abbreviate_location(slot.location_name, limit=14 if price else 18))
    return " · ".join(parts)


def format_price(cost: str | None) -> str:
    """Render a portal cost compactly, adding the currency it omits."""
    cleaned = " ".join((cost or "").split())
    if not cleaned:
        return ""
    if any(symbol in cleaned for symbol in ("€", "EUR", "eur")):
        return cleaned
    return f"{cleaned} €"


def abbreviate_location(name: str, limit: int = 18) -> str:
    cleaned = " ".join(name.split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def format_slot_detail(slot: PortalSlot) -> str:
    lines = [
        f"📅 <b>{escape(format_local_datetime(slot.starts_at_utc))}</b>",
        f"🏥 Sede: {escape(slot.location_name)}",
    ]
    if slot.clinic_name:
        lines.append(f"🚪 Ambulatorio: {escape(slot.clinic_name)}")
    if slot.doctor_name:
        lines.append(f"🩺 Medico: {escape(slot.doctor_name)}")
    if slot.cost:
        lines.append(f"💶 Costo indicato: {escape(slot.cost)}")
    return "\n".join(lines)


def format_account(account: PortalAccount) -> str:
    status = texts.ACCOUNT_STATUS_LABELS.get(account.status.value, account.status.value)
    return f"{escape(account.label)} — {status}"


def format_patient(patient: Patient) -> str:
    name = patient.display_name
    label = mask_person_name(name) if name else "paziente"
    if patient.relation:
        return f"{label} ({escape(patient.relation)})"
    return label


def format_patient_full(patient: Patient) -> str:
    """Full patient name, shown only to the owner of the account."""
    return escape(patient.display_name or "paziente")


def format_weekdays(mask: int) -> str:
    if mask == ALL_WEEKDAYS_MASK:
        return "tutti i giorni"
    selected = [WEEKDAY_SHORT[index] for index in range(7) if mask & (1 << index)]
    return ", ".join(selected) if selected else "nessun giorno"


def format_time_windows(windows: Sequence[TimeWindow]) -> str:
    if not windows:
        return "qualsiasi ora"
    return ", ".join(f"{format_time(w.start)}–{format_time(w.end)}" for w in windows)


def format_excluded_dates(dates: Sequence[date]) -> str:
    if not dates:
        return "nessuna"
    return ", ".join(format_short_date(value) for value in dates)


def format_period(
    *,
    date_mode: DateMode,
    date_from: date | None,
    date_to: date | None,
    rolling_days: int | None,
) -> str:
    if date_mode is DateMode.ROLLING:
        return f"da oggi a +{rolling_days or 0} giorni"
    start = format_short_date(date_from) if date_from else "inizio non impostato"
    end = format_short_date(date_to) if date_to else "fine non impostata"
    return f"dal {start} al {end}"


def format_search(
    *,
    search_type: SearchType | None,
    service_name: str | None,
    doctor_name: str | None,
    query: str | None,
) -> str:
    if search_type is SearchType.DOCTOR:
        return f"medico: {escape(doctor_name or query or '-')}"
    return f"prestazione: {escape(service_name or query or '-')}"


def format_locations(mode: LocationMode, names: Sequence[str]) -> str:
    if mode is LocationMode.ALL_DYNAMIC:
        return "tutte le sedi (aggiornate automaticamente)"
    if not names:
        return "nessuna sede selezionata"
    return ", ".join(escape(name) for name in names)


def format_appointment_card(appointment: AppointmentCache) -> str:
    """Render an appointment card; the patient name is masked in lists."""
    lines = [
        f"📅 <b>{escape(format_local_datetime(appointment.scheduled_at_utc))}</b>",
        f"🔬 {escape(appointment.service_name or 'prestazione non indicata')}",
    ]
    if appointment.doctor_name:
        lines.append(f"🩺 {escape(appointment.doctor_name)}")
    if appointment.location_name:
        lines.append(f"🏥 {escape(appointment.location_name)}")
    if appointment.patient_label:
        lines.append(f"👤 {mask_person_name(appointment.patient_label)}")
    lines.append(f"📌 Stato: {status_label(appointment.status.value)}")
    if appointment.payment_note:
        lines.append(f"💶 {escape(appointment.payment_note)}")
    return "\n".join(lines)


def status_label(value: str) -> str:
    return {
        "PENDING": "in programma",
        "CANCELLED": "disdetto",
        "COMPLETED": "concluso",
        "UNKNOWN": "non determinato",
    }.get(value, value.lower())


def format_watch_row(watch: BookingWatch) -> str:
    status = texts.WATCH_STATUS_LABELS.get(watch.status.value, watch.status.value)
    mode = texts.WATCH_MODE_LABELS.get(watch.mode.value, watch.mode.value)
    bell = "🔔" if watch.notifications_enabled else "🔕"
    return f"{bell} {escape(watch.name)} · {status} · {mode}"


def format_watch_detail(
    watch: BookingWatch,
    *,
    account_label: str,
    patient_label: str,
    location_summary: str,
    time_windows: str,
    excluded: str,
) -> str:
    lines = [
        f"<b>{escape(watch.name)}</b>",
        f"Stato: <b>{texts.WATCH_STATUS_LABELS.get(watch.status.value, watch.status.value)}</b>",
        f"Modalità: <b>{texts.WATCH_MODE_LABELS.get(watch.mode.value, watch.mode.value)}</b>",
        f"Notifiche: <b>{'attive' if watch.notifications_enabled else 'disattivate'}</b>",
        "Avvisi: <b>"
        + ("solo nuovi slot" if watch.notify_only_changes else "riepilogo a ogni controllo")
        + "</b>",
        "",
        f"Account: {escape(account_label)}",
        f"Paziente: {patient_label}",
        f"Ricerca: {
            format_search(
                search_type=watch.search_type,
                service_name=watch.selected_service_name,
                doctor_name=watch.selected_doctor_name,
                query=watch.query_text,
            )
        }",
        f"Sedi: {escape(location_summary)}",
        f"Periodo: {
            format_period(
                date_mode=watch.date_mode,
                date_from=watch.date_from,
                date_to=watch.date_to,
                rolling_days=watch.rolling_days,
            )
        }",
        f"Giorni: {format_weekdays(watch.weekdays_mask)}",
        f"Fasce orarie: {time_windows}",
        f"Date escluse: {excluded}",
        f"Anticipo minimo: {watch.min_lead_hours} ore",
        f"Frequenza: ogni {watch.interval_seconds // 60} minuti",
        "",
        f"Ultimo controllo: {format_local_datetime(watch.last_checked_at)}",
        f"Prossimo controllo: {format_local_datetime(watch.next_check_at)}",
    ]
    if watch.last_error_code:
        lines.append(f"Ultimo errore: <code>{escape(watch.last_error_code)}</code>")
    return "\n".join(lines)


def bullet_list(items: Sequence[str]) -> str:
    return "\n".join(f"• {item}" for item in items) if items else texts.EMPTY_PAGE


def format_watch_summary(
    watch: BookingWatch,
    *,
    account_label: str,
    patient_label: str,
    location_summary: str,
) -> str:
    """Compact list entry: what is monitored, where, and when it runs next."""
    status = texts.WATCH_STATUS_LABELS.get(watch.status.value, watch.status.value)
    mode = texts.WATCH_MODE_LABELS.get(watch.mode.value, watch.mode.value)
    bell = "notifiche attive" if watch.notifications_enabled else "notifiche disattivate"
    return "\n".join(
        [
            f"<b>{escape(watch.name)}</b> — {status}, {mode}, {bell}",
            f"{
                format_search(
                    search_type=watch.search_type,
                    service_name=watch.selected_service_name,
                    doctor_name=watch.selected_doctor_name,
                    query=watch.query_text,
                )
            }",
            f"Account: {escape(account_label)} · Paziente: {patient_label}",
            f"Sedi: {escape(location_summary)}",
            f"Periodo: {
                format_period(
                    date_mode=watch.date_mode,
                    date_from=watch.date_from,
                    date_to=watch.date_to,
                    rolling_days=watch.rolling_days,
                )
            }",
            f"Ultima verifica: {format_local_datetime(watch.last_checked_at)}",
            f"Prossima verifica: {format_local_datetime(watch.next_check_at)}",
        ]
    )
