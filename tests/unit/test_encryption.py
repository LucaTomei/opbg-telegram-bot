"""Encryption, key rotation and blind index unit tests."""

from __future__ import annotations

import pytest

from app.domain.errors import DecryptionError
from app.security.blind_index import BlindIndexService, normalize_lookup_value
from app.security.encryption import (
    EncryptionContext,
    EncryptionService,
    build_associated_data,
    generate_key_material,
)

SECRET = "password-sintetica-non-reale"
CONTEXT = EncryptionContext(table="portal_accounts", record_id="abc123", field="password")


def test_roundtrip_preserves_the_plaintext(encryption: EncryptionService) -> None:
    envelope = encryption.encrypt(SECRET, CONTEXT)
    assert envelope != SECRET.encode("utf-8")
    assert encryption.decrypt_text(envelope, CONTEXT) == SECRET


def test_every_ciphertext_uses_a_fresh_nonce(encryption: EncryptionService) -> None:
    first = encryption.encrypt(SECRET, CONTEXT)
    second = encryption.encrypt(SECRET, CONTEXT)
    assert first != second
    assert encryption.decrypt_text(first, CONTEXT) == encryption.decrypt_text(second, CONTEXT)


def test_ciphertext_cannot_be_moved_to_another_record(encryption: EncryptionService) -> None:
    envelope = encryption.encrypt(SECRET, CONTEXT)
    other = EncryptionContext(table="portal_accounts", record_id="other", field="password")
    with pytest.raises(DecryptionError):
        encryption.decrypt(envelope, other)


def test_ciphertext_cannot_be_moved_to_another_field(encryption: EncryptionService) -> None:
    envelope = encryption.encrypt(SECRET, CONTEXT)
    other = EncryptionContext(table="portal_accounts", record_id="abc123", field="email")
    with pytest.raises(DecryptionError):
        encryption.decrypt(envelope, other)


def test_ciphertext_cannot_be_moved_to_another_table(encryption: EncryptionService) -> None:
    envelope = encryption.encrypt(SECRET, CONTEXT)
    other = EncryptionContext(table="patients", record_id="abc123", field="password")
    with pytest.raises(DecryptionError):
        encryption.decrypt(envelope, other)


def test_tampering_with_the_ciphertext_is_detected(encryption: EncryptionService) -> None:
    envelope = bytearray(encryption.encrypt(SECRET, CONTEXT))
    envelope[-1] ^= 0x01
    with pytest.raises(DecryptionError):
        encryption.decrypt(bytes(envelope), CONTEXT)


def test_truncated_envelope_is_rejected(encryption: EncryptionService) -> None:
    with pytest.raises(DecryptionError):
        encryption.decrypt(b"\x01\x00", CONTEXT)


def test_unknown_envelope_version_is_rejected(encryption: EncryptionService) -> None:
    envelope = bytearray(encryption.encrypt(SECRET, CONTEXT))
    envelope[0] = 9
    with pytest.raises(DecryptionError):
        encryption.decrypt(bytes(envelope), CONTEXT)


def test_a_key_version_that_is_not_loaded_fails_cleanly() -> None:
    old = generate_key_material()
    writer = EncryptionService(active_key=old, active_key_version=1)
    envelope = writer.encrypt(SECRET, CONTEXT)
    reader = EncryptionService(active_key=generate_key_material(), active_key_version=2)
    with pytest.raises(DecryptionError):
        reader.decrypt(envelope, CONTEXT)


def test_previous_keys_allow_online_rotation() -> None:
    old = generate_key_material()
    new = generate_key_material()
    writer = EncryptionService(active_key=old, active_key_version=1)
    envelope = writer.encrypt(SECRET, CONTEXT)

    rotator = EncryptionService(active_key=new, active_key_version=2, previous_keys={1: old})
    assert rotator.needs_rotation(envelope) is True
    assert rotator.decrypt_text(envelope, CONTEXT) == SECRET

    rewrapped = rotator.rewrap(envelope, CONTEXT)
    assert rotator.needs_rotation(rewrapped) is False
    assert EncryptionService.key_version_of(rewrapped) == 2
    assert rotator.decrypt_text(rewrapped, CONTEXT) == SECRET


def test_active_key_cannot_be_redeclared_as_previous() -> None:
    key = generate_key_material()
    with pytest.raises(ValueError, match="previous keys"):
        EncryptionService(active_key=key, active_key_version=1, previous_keys={1: key})


def test_weak_key_material_is_refused() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        EncryptionService(active_key=b"short")


def test_associated_data_requires_table_and_field() -> None:
    with pytest.raises(ValueError, match="required"):
        build_associated_data("", "id", "field", 1)


def test_associated_data_binds_every_component() -> None:
    assert build_associated_data("t", "r", "f", 3) == b"t|r|f|v3"


# --- blind index -------------------------------------------------------------
def test_blind_index_is_deterministic_after_normalisation() -> None:
    service = BlindIndexService(master_key=generate_key_material())
    first = service.compute("Mario.Rossi@Example.INVALID ", table="portal_accounts", field="email")
    second = service.compute("mario.rossi@example.invalid", table="portal_accounts", field="email")
    assert first == second
    assert len(first) == 32


def test_blind_index_is_domain_separated() -> None:
    service = BlindIndexService(master_key=generate_key_material())
    account = service.compute("valore", table="portal_accounts", field="email")
    patient = service.compute("valore", table="patients", field="external_tax_code")
    assert account != patient


def test_blind_index_depends_on_the_key() -> None:
    value = "mario.rossi@example.invalid"
    first = BlindIndexService(master_key=generate_key_material()).compute(
        value, table="portal_accounts", field="email"
    )
    second = BlindIndexService(master_key=generate_key_material()).compute(
        value, table="portal_accounts", field="email"
    )
    assert first != second


def test_blind_index_matching_is_constant_time_and_correct() -> None:
    service = BlindIndexService(master_key=generate_key_material())
    index = service.compute("valore", table="patients", field="external_tax_code")
    assert service.matches("VALORE", index, table="patients", field="external_tax_code")
    assert not service.matches("altro", index, table="patients", field="external_tax_code")


def test_blind_index_rejects_empty_values() -> None:
    service = BlindIndexService(master_key=generate_key_material())
    with pytest.raises(ValueError, match="empty"):
        service.compute("   ", table="patients", field="external_tax_code")


def test_blind_index_requires_a_strong_key() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        BlindIndexService(master_key=b"short")


def test_normalisation_is_unicode_aware() -> None:
    assert normalize_lookup_value("  CAFFÈ  ") == "caffè"
