"""Callback payload encoding unit tests."""

from __future__ import annotations

import uuid

import pytest

from app.bot import callbacks
from app.bot.callbacks import (
    MAX_CALLBACK_BYTES,
    CallbackTooLongError,
    InvalidCallbackError,
    build,
    pack_uuid,
    parse,
    unpack_uuid,
)


def test_uuid_packing_is_compact_and_reversible() -> None:
    value = uuid.uuid4()
    packed = pack_uuid(value)
    assert len(packed) == 22
    assert unpack_uuid(packed) == value


def test_malformed_identifiers_are_rejected() -> None:
    with pytest.raises(InvalidCallbackError):
        unpack_uuid("not-a-uuid")
    with pytest.raises(InvalidCallbackError):
        unpack_uuid("YWJj")


def test_payload_round_trips() -> None:
    identifier = uuid.uuid4()
    payload = build("bk", "slot", identifier, 3)
    parsed = parse(payload)
    assert parsed.namespace == "bk"
    assert parsed.action == "slot"
    assert parsed.uuid_arg(0) == identifier
    assert parsed.int_arg(1) == 3


def test_missing_arguments_use_the_default() -> None:
    parsed = parse(build("bk", "slot"))
    assert parsed.arg(0) == ""
    assert parsed.int_arg(0, 7) == 7


def test_non_numeric_argument_falls_back_to_the_default() -> None:
    parsed = parse(build("bk", "slot", "abc"))
    assert parsed.int_arg(0, 5) == 5


def test_empty_payload_is_rejected() -> None:
    with pytest.raises(InvalidCallbackError):
        parse(None)
    with pytest.raises(InvalidCallbackError):
        parse("")
    with pytest.raises(InvalidCallbackError):
        parse("onlyone")


def test_payloads_stay_within_the_telegram_limit() -> None:
    payload = build("bk", "confirm", uuid.uuid4(), "x" * 20)
    assert len(payload.encode("utf-8")) <= MAX_CALLBACK_BYTES


def test_oversized_payloads_fail_loudly() -> None:
    with pytest.raises(CallbackTooLongError):
        build("bk", "confirm", uuid.uuid4(), "x" * 60)


def test_navigation_helpers_are_stable() -> None:
    assert callbacks.menu() == "nv|menu"
    assert callbacks.cancel() == "nv|cancel"
    assert callbacks.noop() == "nv|noop"


def test_encode_round_trips_a_parsed_callback() -> None:
    payload = build("ac", "open", uuid.uuid4())
    assert parse(payload).encode() == payload


def test_payload_never_contains_personal_data() -> None:
    payload = build("ap", "pdf", uuid.uuid4())
    assert "@" not in payload
    assert payload.count("|") == 2
