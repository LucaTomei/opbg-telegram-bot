"""Serialisation of portal slots for the encrypted draft cache.

Slots are cached inside the encrypted ``booking_drafts`` columns so a callback
only has to carry a draft id and an index, never any slot detail.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from app.domain.entities import PortalLocation, PortalSlot
from app.domain.filters import ROME, to_utc


def slot_to_dict(slot: PortalSlot) -> dict[str, Any]:
    return {
        "proposal_code": slot.proposal_code,
        "starts_at_local": slot.starts_at_local.isoformat(),
        "location_id": slot.location_id,
        "location_name": slot.location_name,
        "clinic_name": slot.clinic_name,
        "agenda_id": slot.agenda_id,
        "agenda_name": slot.agenda_name,
        "service_id": slot.service_id,
        "doctor_id": slot.doctor_id,
        "doctor_name": slot.doctor_name,
        "cost": slot.cost,
        "raw_date_value": slot.raw_date_value,
        "form_fields": slot.form_fields,
    }


def slot_from_dict(payload: dict[str, Any]) -> PortalSlot:
    local = datetime.fromisoformat(str(payload["starts_at_local"]))
    if local.tzinfo is None:
        local = local.replace(tzinfo=ROME)
    return PortalSlot(
        proposal_code=str(payload["proposal_code"]),
        starts_at_local=local,
        starts_at_utc=to_utc(local),
        location_id=str(payload.get("location_id", "")),
        location_name=str(payload.get("location_name", "")),
        clinic_name=payload.get("clinic_name"),
        agenda_id=payload.get("agenda_id"),
        agenda_name=payload.get("agenda_name"),
        service_id=payload.get("service_id"),
        doctor_id=payload.get("doctor_id"),
        doctor_name=payload.get("doctor_name"),
        cost=payload.get("cost"),
        raw_date_value=payload.get("raw_date_value"),
        form_fields=dict(payload.get("form_fields") or {}),
    )


def encode_slots(slots: Sequence[PortalSlot]) -> list[dict[str, Any]]:
    return [slot_to_dict(slot) for slot in slots]


def decode_slots(payload: Any) -> list[PortalSlot]:
    if not isinstance(payload, list):
        return []
    decoded: list[PortalSlot] = []
    for entry in payload:
        if isinstance(entry, dict):
            decoded.append(slot_from_dict(entry))
    return decoded


def encode_locations(locations: Sequence[PortalLocation]) -> list[dict[str, str]]:
    return [
        {"id": location.location_id, "name": location.name, "count": str(location.slot_count)}
        for location in locations
    ]


def decode_locations(payload: Any) -> list[PortalLocation]:
    if not isinstance(payload, list):
        return []
    decoded: list[PortalLocation] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        identifier = str(entry.get("id", ""))
        if not identifier:
            continue
        count_raw = str(entry.get("count", "0"))
        decoded.append(
            PortalLocation(
                location_id=identifier,
                name=str(entry.get("name", identifier)),
                slot_count=int(count_raw) if count_raw.isdigit() else 0,
            )
        )
    return decoded
