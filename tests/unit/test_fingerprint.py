"""Fingerprint and idempotency-key unit tests."""

from __future__ import annotations

import uuid
from datetime import date, time

import pytest

from app.domain.fingerprint import (
    appointment_fingerprint,
    callback_dedup_key,
    idempotency_key,
    notification_dedup_key,
    search_key,
    slot_fingerprint,
    snapshot_hash,
    watch_slot_success_key,
)
from tests.unit.test_filters import slot

ACCOUNT = uuid.UUID("00000000-0000-7000-8000-000000000001")
OTHER_ACCOUNT = uuid.UUID("00000000-0000-7000-8000-000000000002")
PATIENT = uuid.UUID("00000000-0000-7000-8000-0000000000a1")
OTHER_PATIENT = uuid.UUID("00000000-0000-7000-8000-0000000000a2")
KEY = search_key(search_type="SERVICE", service_id="9001", doctor_id=None)


def fingerprint(**overrides: object) -> str:
    payload = {
        "account_id": ACCOUNT,
        "patient_id": PATIENT,
        "search_key": KEY,
    }
    payload.update(overrides)
    target = payload.pop("slot", slot(date(2026, 9, 15), time(9, 30), code="PROP-1"))
    return slot_fingerprint(target, **payload)  # type: ignore[arg-type]


def test_fingerprint_is_deterministic() -> None:
    assert fingerprint() == fingerprint()


def test_fingerprint_changes_with_the_account() -> None:
    assert fingerprint() != fingerprint(account_id=OTHER_ACCOUNT)


def test_fingerprint_changes_with_the_patient() -> None:
    assert fingerprint() != fingerprint(patient_id=OTHER_PATIENT)


def test_fingerprint_changes_with_the_search() -> None:
    other = search_key(search_type="DOCTOR", service_id=None, doctor_id="M-1")
    assert fingerprint() != fingerprint(search_key=other)


@pytest.mark.parametrize(
    "different",
    [
        slot(date(2026, 9, 16), time(9, 30), code="PROP-1"),
        slot(date(2026, 9, 15), time(10, 30), code="PROP-1"),
        slot(date(2026, 9, 15), time(9, 30), code="PROP-2"),
        slot(date(2026, 9, 15), time(9, 30), code="PROP-1", location_id="999"),
        slot(date(2026, 9, 15), time(9, 30), code="PROP-1", doctor_id="M-9"),
    ],
)
def test_fingerprint_changes_with_every_identifying_part(different: object) -> None:
    assert fingerprint() != fingerprint(slot=different)


def test_fingerprint_does_not_leak_the_identifiers() -> None:
    value = fingerprint()
    assert ACCOUNT.hex not in value
    assert PATIENT.hex not in value
    assert len(value) == 40


def test_search_key_distinguishes_service_and_doctor() -> None:
    assert search_key(search_type="SERVICE", service_id="1", doctor_id=None) != search_key(
        search_type="DOCTOR", service_id=None, doctor_id="1"
    )


def test_idempotency_key_is_scoped() -> None:
    manual = idempotency_key(
        account_id=ACCOUNT, patient_id=PATIENT, fingerprint="abc", scope="manual"
    )
    watch = idempotency_key(
        account_id=ACCOUNT, patient_id=PATIENT, fingerprint="abc", scope="watch"
    )
    assert manual != watch
    assert manual == idempotency_key(
        account_id=ACCOUNT, patient_id=PATIENT, fingerprint="abc", scope="manual"
    )


def test_watch_slot_success_key_is_unique_per_watch_and_slot() -> None:
    watch = uuid.uuid4()
    assert watch_slot_success_key(watch, "abc") != watch_slot_success_key(watch, "abd")
    assert watch_slot_success_key(watch, "abc") == watch_slot_success_key(watch, "abc")


def test_snapshot_hash_is_order_independent() -> None:
    assert snapshot_hash(["1", "2", "3"]) == snapshot_hash(["3", "1", "2"])
    assert snapshot_hash(["1", "2"]) != snapshot_hash(["1", "2", "3"])


def test_notification_dedup_key_includes_every_component() -> None:
    user = uuid.uuid4()
    base = notification_dedup_key(event_type="NEW_SLOT", user_id=user, subject="s")
    assert base == notification_dedup_key(event_type="NEW_SLOT", user_id=user, subject="s")
    assert base != notification_dedup_key(event_type="SLOT_LOST", user_id=user, subject="s")
    assert base != notification_dedup_key(event_type="NEW_SLOT", user_id=user, subject="t")
    assert base != notification_dedup_key(event_type="NEW_SLOT", user_id=uuid.uuid4(), subject="s")


def test_callback_dedup_key_distinguishes_updates() -> None:
    first = callback_dedup_key(update_id=1, callback_id="cb", action="wt|del|x")
    assert first == callback_dedup_key(update_id=1, callback_id="cb", action="wt|del|x")
    assert first != callback_dedup_key(update_id=2, callback_id="cb", action="wt|del|x")
    assert first != callback_dedup_key(update_id=1, callback_id="other", action="wt|del|x")


def test_appointment_fingerprint_detects_changes() -> None:
    base = appointment_fingerprint(
        external_id="70001", scheduled_at_iso="2026-09-15T07:30", location="Alfa", service="V"
    )
    moved = appointment_fingerprint(
        external_id="70001", scheduled_at_iso="2026-09-16T07:30", location="Alfa", service="V"
    )
    assert base != moved
