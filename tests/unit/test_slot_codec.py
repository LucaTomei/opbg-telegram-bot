"""Slot and location serialisation unit tests."""

from __future__ import annotations

from datetime import date, time

from app.domain.entities import PortalLocation
from app.services.slot_codec import (
    decode_locations,
    decode_slots,
    encode_locations,
    encode_slots,
    slot_from_dict,
    slot_to_dict,
)
from tests.unit.test_filters import slot


def test_slot_round_trip_preserves_every_field() -> None:
    original = slot(date(2026, 9, 15), time(9, 30), code="PROP-1", doctor_id="M-1")
    restored = slot_from_dict(slot_to_dict(original))
    assert restored.proposal_code == original.proposal_code
    assert restored.starts_at_local == original.starts_at_local
    assert restored.starts_at_utc == original.starts_at_utc
    assert restored.location_id == original.location_id
    assert restored.doctor_id == original.doctor_id


def test_slot_list_round_trip() -> None:
    slots = [
        slot(date(2026, 9, 15), time(9, 30), code="A"),
        slot(date(2026, 9, 16), time(10, 0), code="B"),
    ]
    restored = decode_slots(encode_slots(slots))
    assert [item.proposal_code for item in restored] == ["A", "B"]


def test_decoding_ignores_malformed_entries() -> None:
    assert decode_slots(["not a dict", 42]) == []
    assert decode_slots("not a list") == []


def test_location_round_trip() -> None:
    locations = [PortalLocation(location_id="101", name="Sede Alfa", slot_count=3)]
    restored = decode_locations(encode_locations(locations))
    assert restored[0].location_id == "101"
    assert restored[0].slot_count == 3


def test_locations_without_an_id_are_dropped() -> None:
    assert decode_locations([{"name": "senza id"}, "x"]) == []
