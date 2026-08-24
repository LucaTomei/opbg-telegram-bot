"""Deterministic fingerprints and idempotency keys.

Fingerprints must be stable across processes and restarts, must not leak the
underlying personal data and must change whenever any identifying part of a
slot changes.
"""

from __future__ import annotations

import uuid
from hashlib import sha256

from app.domain.entities import PortalSlot

_FINGERPRINT_LENGTH = 40


def _digest(*parts: str) -> str:
    hasher = sha256()
    for part in parts:
        hasher.update(part.encode("utf-8"))
        hasher.update(b"\x1f")
    return hasher.hexdigest()[:_FINGERPRINT_LENGTH]


def slot_fingerprint(
    slot: PortalSlot,
    *,
    account_id: uuid.UUID,
    patient_id: uuid.UUID,
    search_key: str,
) -> str:
    """Fingerprint identifying one slot for one account, patient and search."""
    return _digest(
        account_id.hex,
        patient_id.hex,
        search_key,
        slot.location_id,
        slot.starts_at_utc.replace(tzinfo=None).isoformat(timespec="minutes"),
        slot.proposal_code,
        slot.doctor_id or "",
    )


def search_key(
    *,
    search_type: str,
    service_id: str | None,
    doctor_id: str | None,
) -> str:
    """Stable key describing what is being searched."""
    return _digest(search_type, service_id or "", doctor_id or "")


def idempotency_key(
    *,
    account_id: uuid.UUID,
    patient_id: uuid.UUID,
    fingerprint: str,
    scope: str,
) -> str:
    """Idempotency key guaranteeing a slot is booked at most once per scope."""
    return _digest(scope, account_id.hex, patient_id.hex, fingerprint)


def watch_slot_success_key(watch_id: uuid.UUID, fingerprint: str) -> str:
    """Unique key enforcing "one successful booking per watch and slot"."""
    return f"{watch_id.hex}:{fingerprint}"


def appointment_fingerprint(
    *,
    external_id: str,
    scheduled_at_iso: str,
    location: str,
    service: str,
) -> str:
    """Fingerprint used to detect appointment changes without storing raw HTML."""
    return _digest(external_id, scheduled_at_iso, location, service)


def snapshot_hash(external_ids: list[str]) -> bytes:
    """Hash of an appointment-list snapshot, used to reconcile bookings."""
    hasher = sha256()
    for external_id in sorted(external_ids):
        hasher.update(external_id.encode("utf-8"))
        hasher.update(b"\x1f")
    return hasher.digest()


def notification_dedup_key(
    *,
    event_type: str,
    user_id: uuid.UUID,
    subject: str,
) -> bytes:
    """Deduplication key for the notification queue."""
    hasher = sha256()
    hasher.update(event_type.encode("utf-8"))
    hasher.update(b"\x1f")
    hasher.update(user_id.bytes)
    hasher.update(b"\x1f")
    hasher.update(subject.encode("utf-8"))
    return hasher.digest()


def callback_dedup_key(*, update_id: int, callback_id: str, action: str) -> bytes:
    """Deduplication key making repeated Telegram callbacks idempotent."""
    hasher = sha256()
    hasher.update(str(update_id).encode("utf-8"))
    hasher.update(b"\x1f")
    hasher.update(callback_id.encode("utf-8"))
    hasher.update(b"\x1f")
    hasher.update(action.encode("utf-8"))
    return hasher.digest()
