"""Parsers turning portal HTML and JSON into domain value objects.

Every parser is defensive: it works from semantic labels and field names rather
than positional indexes, tolerates missing optional keys and raises
:class:`~app.domain.errors.PortalContractChanged` with a redacted detail when
the structure no longer matches.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, time
from typing import Any, Final

import orjson
from selectolax.parser import HTMLParser, Node

from app.domain.entities import (
    PortalAppointment,
    PortalAvailability,
    PortalDoctor,
    PortalLocation,
    PortalPatient,
    PortalService,
    PortalSlot,
)
from app.domain.errors import PortalContractChanged
from app.domain.filters import ROME, combine_local, to_utc
from app.domain.fingerprint import appointment_fingerprint
from app.portal.forms import HtmlForm, extract_form, find_form

PATIENT_SELECT_NAME = "tax_code"

_WHITESPACE_RE = re.compile(r"\s+")
_DATE_PATTERNS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%Y/%m/%d",
)
_DATETIME_PATTERNS: tuple[str, ...] = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%Y-%m-%dT%H:%M:%S",
)
_TIME_RE = re.compile(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b")

_ITALIAN_MONTHS: dict[str, int] = {
    "gennaio": 1,
    "febbraio": 2,
    "marzo": 3,
    "aprile": 4,
    "maggio": 5,
    "giugno": 6,
    "luglio": 7,
    "agosto": 8,
    "settembre": 9,
    "ottobre": 10,
    "novembre": 11,
    "dicembre": 12,
}


def normalize_text(value: str | None) -> str:
    """Collapse whitespace and normalise unicode for comparisons and display."""
    if not value:
        return ""
    return _WHITESPACE_RE.sub(" ", unicodedata.normalize("NFKC", value)).strip()


def normalize_label(value: str | None) -> str:
    """Normalise a field label so lookups do not depend on punctuation or case."""
    text = normalize_text(value).casefold().rstrip(":").strip()
    stripped = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in stripped if not unicodedata.combining(ch))


def parse_date(value: str | None) -> date | None:
    """Parse a date written in any of the formats observed on the portal."""
    text = normalize_text(value)
    if not text:
        return None
    for pattern in _DATE_PATTERNS:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    match = re.search(r"(\d{1,2})\s+([A-Za-zàèéìòù]+)\s+(\d{4})", text)
    if match:
        month = _ITALIAN_MONTHS.get(normalize_label(match.group(2)))
        if month:
            return date(int(match.group(3)), month, int(match.group(1)))
    iso_match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if iso_match:
        return date.fromisoformat(iso_match.group(1))
    return None


def parse_time(value: str | None) -> time | None:
    text = normalize_text(value)
    if not text:
        return None
    match = _TIME_RE.search(text)
    if not match:
        return None
    return time(int(match.group(1)), int(match.group(2)))


def parse_datetime(value: str | None) -> datetime | None:
    """Parse a naive local datetime from a portal string."""
    text = normalize_text(value)
    if not text:
        return None
    for pattern in _DATETIME_PATTERNS:
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    day = parse_date(text)
    if day is None:
        return None
    moment = parse_time(text) or time(0, 0)
    return datetime.combine(day, moment)


# --- Login and session -------------------------------------------------------


def parse_login_form(html: str) -> HtmlForm:
    """Extract the login form and validate the fields the portal expects."""
    document = HTMLParser(html)
    form = find_form(document, action_contains="/login") or find_form(document)
    if form is None:
        raise PortalContractChanged("login form not found")
    if "email" not in form.fields or "password" not in form.fields:
        raise PortalContractChanged("login form does not expose email/password fields")
    return form


def looks_like_login_form(html: str) -> bool:
    """Whether the page is asking for credentials.

    This is the portal saying "you are not logged in" in so many words, and it
    is what separates a refused password from a portal that simply no longer
    looks the way this adapter expects.
    """
    document = HTMLParser(html)
    return document.css_first('form[action*="/login"] input[name="password"]') is not None


def looks_authenticated(html: str) -> bool:
    """Heuristic used together with an explicit verification request."""
    if looks_like_login_form(html):
        return False
    markers = ("/logout", "appointments-list", "appointment-form", "/user/info")
    lowered = html.lower()
    return any(marker in lowered for marker in markers)


def parse_user_info(payload: Any) -> dict[str, Any]:
    """Extract only the non-sensitive parts of ``/user/info``.

    Access and refresh tokens returned by the portal are deliberately dropped:
    the bot does not need them and they must never be stored or logged.
    """
    if not isinstance(payload, Mapping):
        raise PortalContractChanged("user info payload is not an object")
    allowed = ("id", "name", "surname", "nome", "cognome", "user_id", "username")
    metadata: dict[str, Any] = {}
    for key in allowed:
        value = payload.get(key)
        if isinstance(value, str | int):
            metadata[key] = value
    nested = payload.get("user")
    if isinstance(nested, Mapping):
        for key in allowed:
            value = nested.get(key)
            if isinstance(value, str | int):
                metadata.setdefault(key, value)
    return metadata


# --- Patients ----------------------------------------------------------------


def parse_patients(html: str) -> tuple[PortalPatient, ...]:
    """Read the patient options of the LAPI booking form."""
    document = HTMLParser(html)
    form = find_form(document, selector_id="appointment-form") or find_form(
        document, action_contains="/appointments/create"
    )
    options: Sequence[Any]
    if form is not None and PATIENT_SELECT_NAME in form.options:
        options = form.options[PATIENT_SELECT_NAME]
    else:
        select = document.css_first(f'select[name="{PATIENT_SELECT_NAME}"]')
        if select is None:
            raise PortalContractChanged("patient select not found in the booking form")
        options = [
            type(
                "RawOption",
                (),
                {
                    "value": node.attributes.get("value") or "",
                    "label": node.text(strip=True),
                    "disabled": "disabled" in node.attributes,
                },
            )()
            for node in select.css("option")
        ]

    patients: list[PortalPatient] = []
    for option in options:
        value = normalize_text(getattr(option, "value", ""))
        label = normalize_text(getattr(option, "label", ""))
        if not value or getattr(option, "disabled", False):
            continue
        display, relation = _split_patient_label(label)
        patients.append(
            PortalPatient(tax_code=value, display_name=display or value, relation=relation)
        )
    return tuple(patients)


def _split_patient_label(label: str) -> tuple[str, str | None]:
    """Split ``Name Surname (relation)`` into its two parts."""
    match = re.match(r"^(?P<name>.+?)\s*[\(\[](?P<relation>[^\)\]]+)[\)\]]\s*$", label)
    if match:
        return normalize_text(match.group("name")), normalize_text(match.group("relation"))
    if " - " in label:
        head, _, tail = label.partition(" - ")
        return normalize_text(head), normalize_text(tail)
    return label, None


# --- Autocomplete ------------------------------------------------------------


def _iter_service_entries(payload: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(payload, Mapping):
        payload = [payload]
    if not isinstance(payload, Sequence) or isinstance(payload, str | bytes):
        raise PortalContractChanged("service autocomplete payload is not a list")
    for group in payload:
        if not isinstance(group, Mapping):
            continue
        entries = group.get("list")
        if entries is None:
            continue
        if not isinstance(entries, Sequence) or isinstance(entries, str | bytes):
            raise PortalContractChanged("service autocomplete 'list' is not an array")
        for entry in entries:
            if isinstance(entry, Mapping):
                yield entry


def parse_services(payload: Any) -> tuple[PortalService, ...]:
    """Normalise the ``/service-autocomplete`` response."""
    services: list[PortalService] = []
    seen: set[str] = set()
    for entry in _iter_service_entries(payload):
        description = normalize_text(str(entry.get("descPrestazione") or ""))
        service_id = entry.get("idPrestazione")
        if not description or service_id is None:
            continue
        identifier = str(service_id)
        specification = entry.get("idSpecificazione")
        key = f"{identifier}|{specification}"
        if key in seen:
            continue
        seen.add(key)
        services.append(
            PortalService(
                service_id=identifier,
                description=description,
                online_bookable=bool(entry.get("prenOnLine", False))
                and bool(entry.get("disponibilitaVisibileOnLine", True)),
                specification_id=None if specification is None else str(specification),
                group_id=None
                if entry.get("gruppoErogabile") is None
                else str(entry.get("gruppoErogabile")),
                preparation_note=normalize_text(str(entry.get("testoModalitaPreparazione") or ""))
                or None,
                cost=normalize_text(str(entry.get("costoPrestazione") or "")) or None,
                counter_booking=bool(entry.get("prenSportello", False)),
                phone_booking=bool(entry.get("prenTelefonica", False)),
            )
        )
    return tuple(services)


def parse_doctors(payload: Any) -> tuple[PortalDoctor, ...]:
    """Normalise the ``/medici-autocomplete`` response."""
    if isinstance(payload, Mapping):
        payload = [payload]
    if not isinstance(payload, Sequence) or isinstance(payload, str | bytes):
        raise PortalContractChanged("doctor autocomplete payload is not a list")
    doctors: list[PortalDoctor] = []
    seen: set[str] = set()
    for entry in payload:
        if not isinstance(entry, Mapping):
            continue
        description = normalize_text(str(entry.get("descrizione") or ""))
        last_name = normalize_text(str(entry.get("cognome") or ""))
        first_name = normalize_text(str(entry.get("nome") or ""))
        if not description and not last_name:
            continue
        # The portal builds the radio value as ``cognome_nome``; sending the
        # description instead earns a server error on the next step.
        if last_name and first_name:
            raw_value = f"{last_name}_{first_name}"
        elif last_name:
            raw_value = last_name
        else:
            raw_value = description.replace(" ", "_")
        # What the user reads keeps its spaces, whatever the portal wants back.
        display = description or f"{last_name} {first_name}".strip()
        if raw_value in seen:
            continue
        seen.add(raw_value)
        doctors.append(
            PortalDoctor(
                raw_value=raw_value,
                description=display,
                last_name=last_name,
                first_name=first_name,
                gender=normalize_text(str(entry.get("sesso") or "")) or None,
            )
        )
    return tuple(doctors)


# --- Availability ------------------------------------------------------------


def _coerce_slot_datetime(day_key: str, time_key: str, proposal: Mapping[str, Any]) -> datetime:
    day = parse_date(day_key)
    moment = parse_time(time_key)
    if day is None or moment is None:
        parsed = parse_datetime(str(proposal.get("dataProposta") or ""))
        if parsed is None:
            raise PortalContractChanged("availability entry has no parsable date/time")
        return parsed
    return datetime.combine(day, moment)


def _as_mapping(value: Any, field: str) -> Mapping[str, Any]:
    """Read a portal field that is a map, tolerating an empty JSON array.

    The portal is written in PHP, where an empty associative array serialises
    as ``[]`` rather than ``{}``. An empty list therefore means "no entries",
    not a broken contract: rejecting it turned every search without
    availability into a technical error.
    """
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return value
    if isinstance(value, Sequence) and not isinstance(value, str | bytes) and len(value) == 0:
        return {}
    raise PortalContractChanged(f"availability {field!r} is not an object")


def parse_availability(payload: Any) -> PortalAvailability:
    """Normalise the ``/appointments/disponibilita`` JSON response."""
    if not isinstance(payload, Mapping):
        raise PortalContractChanged("availability payload is not an object")

    locations: dict[str, str] = {}
    for key, value in _as_mapping(payload.get("locations"), "locations").items():
        locations[str(key)] = normalize_text(str(value))

    doctors: dict[str, str] = {}
    for key, value in _as_mapping(payload.get("doctors"), "doctors").items():
        doctors[str(key)] = normalize_text(str(value))

    slots: list[PortalSlot] = []
    counts: dict[str, int] = {}
    dates_locations = _as_mapping(payload.get("dates_locations"), "dates_locations")

    for day_key, per_time in dates_locations.items():
        if not isinstance(per_time, Mapping):
            continue
        for time_key, proposals in per_time.items():
            if not isinstance(proposals, Sequence) or isinstance(proposals, str | bytes):
                continue
            for proposal in proposals:
                if not isinstance(proposal, Mapping):
                    continue
                slot = _build_slot(str(day_key), str(time_key), proposal, locations, doctors)
                if slot is None:
                    continue
                counts[slot.location_id] = counts.get(slot.location_id, 0) + 1
                slots.append(slot)

    location_objects = tuple(
        PortalLocation(location_id=key, name=name or key, slot_count=counts.get(key, 0))
        for key, name in sorted(locations.items(), key=lambda item: item[1] or item[0])
    )
    if not location_objects and slots:
        location_objects = tuple(
            PortalLocation(
                location_id=location_id,
                name=next(s.location_name for s in slots if s.location_id == location_id),
                slot_count=count,
            )
            for location_id, count in sorted(counts.items())
        )
    return PortalAvailability(locations=location_objects, slots=tuple(slots), doctors=doctors)


def _build_slot(
    day_key: str,
    time_key: str,
    proposal: Mapping[str, Any],
    locations: Mapping[str, str],
    doctors: Mapping[str, str],
) -> PortalSlot | None:
    proposal_code = normalize_text(str(proposal.get("codiceProposta") or ""))
    if not proposal_code:
        return None
    local_naive = _coerce_slot_datetime(day_key, time_key, proposal)
    local_aware = combine_local(local_naive.date(), local_naive.time(), ROME)
    location_id = str(
        proposal.get("idStrutturaErogante")
        or proposal.get("idUnitaErogante")
        or proposal.get("idAmbulatorio")
        or ""
    )
    location_name = normalize_text(
        str(proposal.get("descStrutturaErogante") or locations.get(location_id) or "")
    )
    if not location_id:
        location_id = location_name or proposal_code
    doctor_id = proposal.get("idMedico") or proposal.get("medico")
    doctor_key = None if doctor_id is None else str(doctor_id)
    return PortalSlot(
        proposal_code=proposal_code,
        starts_at_local=local_aware,
        starts_at_utc=to_utc(local_aware),
        location_id=location_id,
        location_name=location_name or location_id,
        clinic_name=normalize_text(str(proposal.get("descAmbulatorio") or "")) or None,
        agenda_id=None if proposal.get("idAgenda") is None else str(proposal.get("idAgenda")),
        agenda_name=normalize_text(str(proposal.get("descAgenda") or "")) or None,
        service_id=None
        if proposal.get("idPrestazione") is None
        else str(proposal.get("idPrestazione")),
        doctor_id=doctor_key,
        doctor_name=(
            normalize_text(str(proposal.get("descMedico") or ""))
            or (doctors.get(doctor_key) if doctor_key else None)
            or None
        ),
        cost=normalize_text(str(proposal.get("costoPrestazione") or "")) or None,
        raw_date_value=normalize_text(str(proposal.get("dataProposta") or "")) or None,
        form_fields={
            key: normalize_text(str(value))
            for key, value in proposal.items()
            if isinstance(value, str | int | float) and value is not None
        },
    )


def parse_server_rendered_availability(html: str) -> PortalAvailability:
    """Parse availability rendered directly in the HTML of the next step.

    Each proposal is expected to be a container carrying ``data-*`` attributes
    or hidden inputs; both shapes have been observed on the portal.
    """
    document = HTMLParser(html)
    slots: list[PortalSlot] = []
    locations: dict[str, str] = {}

    for node in document.css("[data-proposal-code], [data-id-proposal], .availability-slot"):
        slot = _slot_from_node(node)
        if slot is None:
            continue
        locations.setdefault(slot.location_id, slot.location_name)
        slots.append(slot)

    if not slots:
        for form_node in document.css("form"):
            hidden = {
                element.attributes.get("name") or "": element.attributes.get("value") or ""
                for element in form_node.css('input[type="hidden"]')
            }
            if not hidden.get("id_proposal"):
                continue
            slot = _slot_from_hidden_fields(hidden)
            if slot is None:
                continue
            locations.setdefault(slot.location_id, slot.location_name)
            slots.append(slot)

    counts: dict[str, int] = {}
    for slot in slots:
        counts[slot.location_id] = counts.get(slot.location_id, 0) + 1
    return PortalAvailability(
        locations=tuple(
            PortalLocation(location_id=key, name=name, slot_count=counts.get(key, 0))
            for key, name in sorted(locations.items(), key=lambda item: item[1])
        ),
        slots=tuple(slots),
    )


def _slot_from_node(node: Node) -> PortalSlot | None:
    attributes = node.attributes
    proposal_code = normalize_text(
        attributes.get("data-proposal-code") or attributes.get("data-id-proposal") or ""
    )
    if not proposal_code:
        return None
    day = parse_date(attributes.get("data-date"))
    moment = parse_time(attributes.get("data-time") or node.text(strip=True))
    if day is None or moment is None:
        return None
    local_aware = combine_local(day, moment, ROME)
    location_id = normalize_text(attributes.get("data-location-id") or "")
    location_name = normalize_text(attributes.get("data-location-name") or "")
    if not location_id:
        location_id = location_name or proposal_code
    return PortalSlot(
        proposal_code=proposal_code,
        starts_at_local=local_aware,
        starts_at_utc=to_utc(local_aware),
        location_id=location_id,
        location_name=location_name or location_id,
        doctor_id=normalize_text(attributes.get("data-doctor-id") or "") or None,
        doctor_name=normalize_text(attributes.get("data-doctor-name") or "") or None,
        service_id=normalize_text(attributes.get("data-service-id") or "") or None,
        raw_date_value=attributes.get("data-date"),
        form_fields={
            key.removeprefix("data-").replace("-", "_"): normalize_text(value or "")
            for key, value in attributes.items()
            if key.startswith("data-")
        },
    )


def _slot_from_hidden_fields(hidden: Mapping[str, str]) -> PortalSlot | None:
    proposal_code = normalize_text(hidden.get("id_proposal", ""))
    if not proposal_code:
        return None
    parsed = parse_datetime(hidden.get("selected_date", ""))
    if parsed is None:
        return None
    local_aware = combine_local(parsed.date(), parsed.time(), ROME)
    location_name = normalize_text(hidden.get("location_name", ""))
    location_id = normalize_text(hidden.get("location_details", "")) or location_name
    return PortalSlot(
        proposal_code=proposal_code,
        starts_at_local=local_aware,
        starts_at_utc=to_utc(local_aware),
        location_id=location_id or proposal_code,
        location_name=location_name or location_id or proposal_code,
        service_id=normalize_text(hidden.get("session_id_prestazione", "")) or None,
        raw_date_value=hidden.get("selected_date"),
        form_fields=dict(hidden),
    )


# --- Appointments ------------------------------------------------------------

_LABEL_ALIASES: dict[str, tuple[str, ...]] = {
    "service": ("prestazione", "esame", "visita", "servizio"),
    "doctor": ("medico", "specialista", "dottore", "professionista"),
    "patient": ("paziente", "assistito", "intestatario"),
    "location": ("sede", "struttura", "presidio", "ambulatorio", "luogo"),
    "date": ("data", "giorno", "data appuntamento"),
    "time": ("ora", "orario", "ore"),
    "status": ("stato", "situazione"),
    "payment": ("pagamento", "costo", "ticket", "importo"),
    "datetime": ("data e ora", "quando", "appuntamento"),
}


def _classify_label(label: str) -> str | None:
    normalized = normalize_label(label)
    for key, aliases in _LABEL_ALIASES.items():
        if normalized in aliases:
            return key
    for key, aliases in _LABEL_ALIASES.items():
        if any(normalized.startswith(alias) for alias in aliases):
            return key
    return None


def _extract_external_id(node: Node) -> str | None:
    for attribute in ("data-appointment-id", "data-id", "id"):
        value = node.attributes.get(attribute)
        if value:
            digits = re.sub(r"[^0-9A-Za-z_-]", "", value)
            match = re.search(r"(\d+)$", digits)
            if match:
                return match.group(1)
            if digits:
                return digits
    for link in node.css("a[href]"):
        href = link.attributes.get("href") or ""
        match = re.search(r"/appointments/([^/]+)/(?:pdf-download|cancel)", href)
        if match:
            return match.group(1)
    cancel = node.css_first("[data-href]")
    if cancel is not None:
        match = re.search(r"/appointments/([^/]+)/cancel", cancel.attributes.get("data-href") or "")
        if match:
            return match.group(1)
    return None


def _status_from_classes(node: Node) -> str | None:
    classes = (node.attributes.get("class") or "").lower().split()
    for candidate in ("cancelled", "canceled", "annullato", "completed", "done", "pending"):
        if candidate in classes:
            return candidate
    return None


def parse_appointments(html: str) -> tuple[PortalAppointment, ...]:
    """Parse the appointment list using normalised labels, not field order."""
    document = HTMLParser(html)
    container = document.css_first("#appointments-list")
    if container is None:
        # Without the documented container the page may still be a valid list
        # rendered differently; only accept it when appointment cards exist.
        fallback = document.body
        if fallback is None or not fallback.css(".appointment, [data-appointment-id]"):
            raise PortalContractChanged("appointment list container not found")
        container = fallback

    nodes = container.css(".appointment")
    if not nodes:
        nodes = container.css("[data-appointment-id]")
    appointments: list[PortalAppointment] = []
    for node in nodes:
        appointment = _parse_appointment_node(node)
        if appointment is not None:
            appointments.append(appointment)
    return tuple(appointments)


def _header_date(node: Node) -> date | None:
    """Read the day from the header of an appointment card."""
    header = node.css_first(".appointment-header")
    if header is None:
        return None
    return parse_date(header.text(deep=False, strip=True)) or parse_date(header.text(strip=True))


def _appointment_values(node: Node) -> dict[str, str]:
    """Read the labelled fields of a card, block by block.

    The block itself disambiguates the label: the portal writes ``Stato`` over
    the payment, so reading label and text as two parallel lists files "not
    paid" as the appointment status.
    """
    values: dict[str, str] = {}
    for block in node.css(".appointment-info"):
        classes = (block.attributes.get("class") or "").lower()
        labels = block.css(".appointment-info__label")
        texts = block.css(".appointment-info__text")
        for label_node, text_node in zip(labels, texts, strict=False):
            key = (
                "payment" if "payment" in classes else _classify_label(label_node.text(strip=True))
            )
            if key is None:
                continue
            values.setdefault(key, normalize_text(text_node.text(strip=True)))
    if values:
        return values

    # A card rendered without the block wrapper: fall back to parallel lists.
    labels = node.css(".appointment-info__label")
    texts = node.css(".appointment-info__text")
    for label_node, text_node in zip(labels, texts, strict=False):
        key = _classify_label(label_node.text(strip=True))
        if key is None:
            continue
        values.setdefault(key, normalize_text(text_node.text(strip=True)))
    return values


def _parse_appointment_node(node: Node) -> PortalAppointment | None:
    values = _appointment_values(node)

    for info in node.css("[data-label]"):
        key = _classify_label(info.attributes.get("data-label") or "")
        if key is not None:
            values.setdefault(key, normalize_text(info.text(strip=True)))

    external_id = _extract_external_id(node)
    if external_id is None:
        return None

    day = parse_date(values.get("date") or values.get("datetime"))
    if day is None:
        # The card carries the day in its header and only the hour among the
        # labelled fields, so a card with no "Data" label is not undated.
        day = _header_date(node)
    moment = parse_time(values.get("time") or values.get("datetime") or "")
    scheduled_local = None
    scheduled_utc = None
    if day is not None:
        scheduled_local = combine_local(day, moment or time(0, 0), ROME)
        scheduled_utc = to_utc(scheduled_local)

    cancel_node = node.css_first(".btn-appointment-list--cancel") or node.css_first(
        "a[href*='/cancel']"
    )
    cancel_path = None
    if cancel_node is not None:
        cancel_path = cancel_node.attributes.get("data-href") or cancel_node.attributes.get("href")

    pdf_node = node.css_first("a[href*='pdf-download']")
    pdf_path = pdf_node.attributes.get("href") if pdf_node is not None else None

    # The card's own class is the portal's verdict; a labelled field is only a
    # hint and may well be about the payment.
    status_hint = _status_from_classes(node) or values.get("status")
    service = values.get("service")
    location = values.get("location")

    return PortalAppointment(
        external_id=external_id,
        service_name=service,
        doctor_name=values.get("doctor"),
        patient_label=values.get("patient"),
        location_name=location,
        scheduled_at_local=scheduled_local,
        scheduled_at_utc=scheduled_utc,
        status_hint=status_hint,
        payment_note=values.get("payment"),
        cancel_path=cancel_path,
        pdf_path=pdf_path,
        raw_fingerprint=appointment_fingerprint(
            external_id=external_id,
            scheduled_at_iso=scheduled_utc.isoformat() if scheduled_utc else "",
            location=location or "",
            service=service or "",
        ),
    )


def parse_confirmation_form(html: str) -> HtmlForm:
    """Extract the final confirmation form of the booking wizard."""
    document = HTMLParser(html)
    form = find_form(document, selector_id="appointment-form") or extract_form(
        document, action_contains="/appointments/create"
    )
    if not form.has_submit and "confirm" not in form.fields:
        raise PortalContractChanged("confirmation form has no submit control")
    return form


def parse_slot_selection_form(html: str, proposal_code: str) -> HtmlForm:
    """Extract the form used to submit a specific proposal."""
    document = HTMLParser(html)
    for form_node in document.css("form"):
        hidden = {
            element.attributes.get("name") or "": element.attributes.get("value") or ""
            for element in form_node.css('input[type="hidden"]')
        }
        if hidden.get("id_proposal") == proposal_code:
            return extract_form(
                HTMLParser(form_node.html or ""),
                action_contains=form_node.attributes.get("action") or "/appointments/create",
            )
    raise PortalContractChanged("no form found for the requested proposal")


#: The radio that stands for "every location" rather than for a location.
LOCATION_ALL: Final[str] = "location-all"
#: Each real location radio is valued ``location-<id>``.
LOCATION_PREFIX: Final[str] = "location-"


def _label_for(document: HTMLParser, element: Node) -> str:
    """The visible label of a form control, by ``for`` or by ``data-label``."""
    element_id = element.attributes.get("id")
    if element_id:
        label = document.css_first(f'label[for="{element_id}"]')
        if label is not None:
            return normalize_text(label.text(strip=True))
    return normalize_text(element.attributes.get("data-label") or "")


def parse_location_options(html: str) -> tuple[PortalLocation, ...]:
    """Read the selectable locations rendered after the search choice.

    The portal renders one radio per location, named ``location`` and valued
    ``location-<id>``, plus a ``location-all`` entry that stands for "every
    location" and is not one itself. Older shapes named ``idUnitaErogante`` and
    a plain ``<select>`` are still handled.

    An empty result simply means the availability endpoint must resolve the
    locations itself.
    """
    document = HTMLParser(html)
    locations: dict[str, str] = {}

    for element in document.css('input[name="location"]'):
        raw = normalize_text(element.attributes.get("value") or "")
        if not raw or raw == LOCATION_ALL:
            continue
        value = raw.removeprefix(LOCATION_PREFIX)
        if not value:
            continue
        locations[value] = _label_for(document, element) or value

    for element in document.css('input[name="idUnitaErogante[]"], input[name="idUnitaErogante"]'):
        value = normalize_text(element.attributes.get("value") or "")
        if not value:
            continue
        locations.setdefault(value, _label_for(document, element) or value)

    for select in document.css('select[name="idUnitaErogante[]"], select[name="idUnitaErogante"]'):
        for option in select.css("option"):
            value = normalize_text(option.attributes.get("value") or "")
            if value:
                locations.setdefault(value, normalize_text(option.text(strip=True)) or value)

    for node in document.css("[data-location-id]"):
        value = normalize_text(node.attributes.get("data-location-id") or "")
        if value:
            locations.setdefault(
                value,
                normalize_text(node.attributes.get("data-location-name") or node.text(strip=True))
                or value,
            )

    return tuple(
        PortalLocation(location_id=key, name=name)
        for key, name in sorted(locations.items(), key=lambda item: item[1])
    )


def parse_form_fields(html: str, *, form_id: str = "appointment-form") -> dict[str, str]:
    """Read the fields of a wizard form as the browser would resubmit them.

    Radios and checkboxes are left out: they carry the choice the user is about
    to make, and the caller supplies it. Everything else is the state the portal
    expects to receive back, and omitting part of it makes the doctor step fail
    with a server error.
    """
    document = HTMLParser(html)
    form = document.css_first(f"form#{form_id}")
    if form is None:
        return {}
    fields: dict[str, str] = {}
    for element in form.css("input, select, textarea"):
        name = element.attributes.get("name") or ""
        kind = (element.attributes.get("type") or element.tag).lower()
        if not name or kind in {"radio", "checkbox", "password", "submit", "button", "file"}:
            continue
        if element.tag == "select":
            chosen = element.css_first("option[selected]")
            fields[name] = normalize_text(chosen.attributes.get("value") or "") if chosen else ""
            continue
        fields[name] = normalize_text(element.attributes.get("value") or "")
    return fields


#: ``#unitaEroganti`` holds unescaped JSON, so it is read from the source.
_DOCTOR_UNITS_RE: Final[re.Pattern[str]] = re.compile(
    r'id="unitaEroganti"\s+value="(.*?)"\s*/?>', re.DOTALL
)


def parse_doctor_services(html: str) -> tuple[PortalService, ...]:
    """Read the services a chosen doctor offers, from ``#unitaEroganti``.

    Picking a doctor is not enough to ask for availability: the portal wants a
    service too, and the step lists the ones that doctor performs in each of
    their locations. The field holds JSON, so a shape it does not have is a
    contract change rather than an empty result.
    """
    # The portal writes the JSON straight into the attribute without escaping
    # its quotes, so an HTML parser truncates the value at the first one. The
    # source has to be read directly.
    match = _DOCTOR_UNITS_RE.search(html)
    if match is None:
        return ()
    raw = match.group(1).strip()
    if not raw or raw == "[]":
        return ()
    try:
        units = orjson.loads(raw)
    except orjson.JSONDecodeError as exc:
        raise PortalContractChanged("the doctor units field is not valid JSON") from exc
    if not isinstance(units, Sequence) or isinstance(units, str | bytes):
        raise PortalContractChanged("the doctor units field is not a list")

    services: dict[str, str] = {}
    for unit in units:
        if not isinstance(unit, Mapping):
            continue
        offered = unit.get("prestazioni")
        if not isinstance(offered, Mapping):
            continue
        for identifier, description in offered.items():
            services.setdefault(str(identifier), normalize_text(str(description)))
    return tuple(
        PortalService(service_id=identifier, description=description, online_bookable=True)
        for identifier, description in sorted(services.items(), key=lambda item: item[1])
    )


def parse_session_service_fields(html: str) -> dict[str, str]:
    """Read the hidden ``session_*_prestazione`` fields kept between steps."""
    document = HTMLParser(html)
    fields: dict[str, str] = {}
    for element in document.css('input[type="hidden"]'):
        name = element.attributes.get("name") or ""
        if name.startswith("session_") or name in {
            "id_specification",
            "servicetype",
            "service_type",
        }:
            fields[name] = normalize_text(element.attributes.get("value") or "")
    return fields


def detect_portal_challenge(html: str) -> str | None:
    """Detect a CAPTCHA or second-factor challenge that requires a human."""
    lowered = html.lower()
    for marker in ("g-recaptcha", "recaptcha", "hcaptcha", "captcha", "cf-turnstile"):
        if marker in lowered:
            return marker
    for marker in ("two-factor", "two_factor", "otp-code", 'name="otp"'):
        if marker in lowered:
            return "second-factor"
    return None


def parse_error_messages(html: str) -> tuple[str, ...]:
    """Collect user-visible validation messages, normalised and truncated."""
    document = HTMLParser(html)
    messages: list[str] = []
    for node in document.css(".alert-danger, .invalid-feedback, .error, [role=alert]"):
        text = normalize_text(node.text(strip=True))
        if text:
            messages.append(text[:200])
    return tuple(dict.fromkeys(messages))[:5]
