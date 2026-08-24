"""One-time token and nonce unit tests."""

from __future__ import annotations

import pytest

from app.security import tokens


def test_generated_tokens_are_unique_and_long() -> None:
    generated = {tokens.generate_token() for _ in range(200)}
    assert len(generated) == 200
    assert all(len(value) >= 40 for value in generated)


def test_only_the_hash_is_persisted() -> None:
    token = tokens.generate_token()
    digest = tokens.hash_token(token)
    assert len(digest) == 32
    assert token.encode("utf-8") not in digest


def test_token_verification_is_exact() -> None:
    token = tokens.generate_token()
    digest = tokens.hash_token(token)
    assert tokens.verify_token(token, digest) is True
    assert tokens.verify_token(token + "x", digest) is False


def test_invite_codes_are_human_typable() -> None:
    code = tokens.generate_invite_code()
    assert len(code) == 14
    assert code.count("-") == 2
    assert set(code.replace("-", "")) <= set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")


def test_invite_codes_are_case_and_separator_insensitive() -> None:
    code = tokens.generate_invite_code()
    digest = tokens.hash_invite_code(code)
    assert tokens.verify_invite_code(code.lower(), digest) is True
    assert tokens.verify_invite_code(code.replace("-", " "), digest) is True
    assert tokens.verify_invite_code("AAAA-BBBB-CCCC", digest) is False


def test_empty_invite_code_is_rejected() -> None:
    with pytest.raises(ValueError, match="empty"):
        tokens.hash_invite_code("   ")
    assert tokens.verify_invite_code("", b"\x00" * 32) is False


def test_context_hash_binds_every_part() -> None:
    first = tokens.hash_context("book", "slot-1", "2026-09-15")
    second = tokens.hash_context("book", "slot-1", "2026-09-16")
    assert first != second
    assert tokens.hash_context("book", "slot-1", "2026-09-15") == first


def test_context_hash_is_not_ambiguous_across_boundaries() -> None:
    assert tokens.hash_context("ab", "c") != tokens.hash_context("a", "bc")


def test_content_hash_is_hex_and_stable() -> None:
    digest = tokens.content_hash("ACTIVE")
    assert len(digest) == 64
    assert digest == tokens.content_hash("ACTIVE")
    assert digest != tokens.content_hash("SUSPENDED")


def test_nonces_are_unique() -> None:
    assert len({tokens.generate_nonce() for _ in range(200)}) == 200


# --- pepper ------------------------------------------------------------------
def test_the_digest_depends_on_the_application_secret() -> None:
    """A database leak alone must not allow verifying a guessed code."""
    from app.security.tokens import TokenHasher

    value = "ABCD-EFGH-JKLM"
    first = TokenHasher(secret=bytes(range(32))).hash(value)
    second = TokenHasher(secret=bytes(range(32, 64))).hash(value)
    assert first != second
    assert len(first) == 32


def test_the_hasher_verifies_in_constant_time() -> None:
    from app.security.tokens import TokenHasher

    hasher = TokenHasher(secret=bytes(range(32)))
    digest = hasher.hash("valore")
    assert hasher.verify("valore", digest) is True
    assert hasher.verify("altro", digest) is False


def test_a_weak_pepper_is_refused() -> None:
    from app.security.tokens import TokenHasher

    with pytest.raises(ValueError, match="32 bytes"):
        TokenHasher(secret=b"corto")


def test_the_plaintext_never_appears_in_the_digest() -> None:
    code = tokens.generate_invite_code()
    digest = tokens.hash_invite_code(code)
    assert tokens.normalize_invite_code(code).encode() not in digest
