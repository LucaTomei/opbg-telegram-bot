"""Secrets must never reach a log, an admin screen or a persisted payload."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.composition import Container
from app.db.models.users import TelegramUser
from app.db.repositories.base import utc_now
from app.db.repositories.system import SystemEventRepository
from app.domain.enums import Severity, UserRole, UserStatus
from app.domain.errors import PermissionDeniedError
from app.security.redaction import REDACTED, redact_value
from app.services.admin import AdminService, require_admin
from tests.support.factories import SYNTHETIC_PASSWORD, make_account, make_patient

ROOT = Path(__file__).resolve().parents[2]


async def test_the_admin_service_exposes_no_secret_accessor() -> None:
    """No administrative method may return a password, a cookie or a token."""
    forbidden = {"password", "cookie", "token", "csrf", "key"}
    for name, _member in inspect.getmembers(AdminService, inspect.isfunction):
        if name.startswith("_"):
            continue
        assert not any(word in name.lower() for word in forbidden), name


async def test_revealing_an_account_returns_no_credential(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    account = await make_account(clean_session, user)
    admin = user
    admin.role = UserRole.ADMIN
    admin.status = UserStatus.ACTIVE
    revealed = await container.admin.reveal_account(
        clean_session, admin, account_id=account.id, reason="verifica di test"
    )
    joined = " ".join(revealed.values())
    assert SYNTHETIC_PASSWORD not in joined
    assert "opbg-prenotazione" not in joined
    assert set(revealed) == {
        "label",
        "email",
        "status",
        "last_login_at",
        "last_sync_at",
        "last_error_code",
    }


async def test_revealing_a_patient_returns_no_extra_field(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    account = await make_account(clean_session, user)
    patient = await make_patient(clean_session, account)
    user.role = UserRole.ADMIN
    revealed = await container.admin.reveal_patient(
        clean_session, user, patient_id=patient.id, reason="verifica di test"
    )
    assert set(revealed) == {"display_name", "tax_code", "relationship", "active"}


async def test_a_non_administrator_is_rejected(user: TelegramUser) -> None:
    user.role = UserRole.USER
    with pytest.raises(PermissionDeniedError):
        require_admin(user)


async def test_a_suspended_administrator_is_rejected(user: TelegramUser) -> None:
    user.role = UserRole.ADMIN
    user.status = UserStatus.SUSPENDED
    with pytest.raises(PermissionDeniedError):
        require_admin(user)


async def test_system_events_store_redacted_messages(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    events = SystemEventRepository(clean_session)
    await events.record(
        severity=Severity.ERROR,
        component="portal",
        event_code="TEST",
        message="login fallito per mario.rossi@example.invalid",
        context={"password": "hunter2", "cookie": "opbg-prenotazione=abc"},
    )
    rows = (
        await clean_session.execute(text("SELECT message, context_json FROM system_events"))
    ).all()
    for message, context in rows:
        assert "mario.rossi@example.invalid" not in message
        assert "hunter2" not in str(context)
        assert "abc" not in str(context)


async def test_no_plaintext_credential_reaches_the_database(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    account = await make_account(clean_session, user)
    await make_patient(clean_session, account)
    await clean_session.flush()
    rows = (
        await clean_session.execute(
            text(
                "SELECT email_ciphertext, password_ciphertext, cookie_jar_ciphertext "
                "FROM portal_accounts"
            )
        )
    ).all()
    assert rows
    for email, password, cookies in rows:
        blob = bytes(email or b"") + bytes(password or b"") + bytes(cookies or b"")
        assert SYNTHETIC_PASSWORD.encode() not in blob
        assert b"utente.sintetico@example.invalid" not in blob


async def test_patient_tax_codes_are_never_stored_in_clear(
    clean_session: AsyncSession, user: TelegramUser
) -> None:
    account = await make_account(clean_session, user)
    await make_patient(clean_session, account)
    await clean_session.flush()
    rows = (
        await clean_session.execute(text("SELECT external_tax_code_ciphertext FROM patients"))
    ).all()
    assert rows
    for (ciphertext,) in rows:
        assert b"AAABBB01A01A001A" not in bytes(ciphertext)


def test_no_source_file_logs_a_raw_response() -> None:
    """Nothing in the application may log a portal body or a credential."""
    offenders: list[str] = []
    for path in (ROOT / "app").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for number, line in enumerate(source.splitlines(), start=1):
            stripped = line.strip()
            if not stripped.startswith(("logger.", "log_event(", "print(")):
                continue
            if any(
                marker in stripped
                for marker in ("response.text", "response.content", ".password", "cookie_jar")
            ):
                offenders.append(f"{path.relative_to(ROOT)}:{number}")
    assert offenders == []


def test_redaction_covers_every_documented_field() -> None:
    payload = {
        "password": "x",
        "cookie": "y",
        "Set-Cookie": "z",
        "_token": "t",
        "csrf_token": "c",
        "access_token": "a",
        "refresh_token": "r",
        "tax_code": "AAABBB01A01A001A",
        "email": "a@b.invalid",
        "raw_html": "<html></html>",
    }
    redacted = redact_value(payload)
    assert all(value == REDACTED for value in redacted.values())


async def test_admin_actions_always_carry_a_reason(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    from app.db.repositories.audit import AuditRepository

    user.role = UserRole.ADMIN
    account = await make_account(clean_session, user)
    await container.admin.reveal_account(
        clean_session, user, account_id=account.id, reason="motivo tracciato"
    )
    entries = await AuditRepository(clean_session).list_entries(page=0, page_size=10)
    assert entries.total == 1
    assert entries.items[0].reason == "motivo tracciato"
    assert entries.items[0].created_at <= utc_now()


def test_no_sensitive_column_is_stored_in_plaintext() -> None:
    """Every column that can hold personal data must be a ciphertext or a hash."""
    from app.db.models import Base

    markers = (
        "email",
        "password",
        "cookie",
        "tax_code",
        "first_name",
        "last_name",
        "username",
        "display_name",
        "relationship",
        "patient_label",
        "payload",
        "service_name",
        "doctor_name",
    )
    # Public or non-personal values, plus the free-text audit reason, which the
    # documentation requires to stay readable.
    allowed = {
        ("watch_locations", "location_name"),
        ("seen_slots", "location_name"),
        ("booking_attempts", "location_name"),
        ("booking_watches", "selected_service_name"),
        ("booking_watches", "selected_doctor_name"),
        ("booking_drafts", "selected_service_name"),
        ("booking_drafts", "selected_doctor_name"),
    }
    offenders = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.tables.values()
        for column in table.columns
        if any(marker in column.name for marker in markers)
        and not column.name.endswith(("_ciphertext", "_blind_index", "_hash"))
        and (table.name, column.name) not in allowed
    ]
    assert offenders == []


def test_the_web_surface_exposes_only_the_expected_routes() -> None:
    """No administrative API may be reachable over HTTP."""
    import re

    sources = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / "app" / "web" / "routes").rglob("*.py")
    )
    routes = set(re.findall(r'@router\.(?:get|post)\("([^"]+)"', sources))
    assert routes == {
        "/connect/{token}",
        "/download/{token}",
        "/health/live",
        "/health/ready",
        "/version",
    }
