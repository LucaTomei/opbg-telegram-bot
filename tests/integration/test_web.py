"""The public web surface: connect form, PDF links and health probes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.composition import Container
from app.config import Settings
from app.db.models.users import TelegramUser
from app.db.repositories.accounts import (
    ConnectTicketRepository,
    DownloadTokenRepository,
    PortalAccountRepository,
)
from app.db.repositories.base import utc_now
from app.db.repositories.bookings import AppointmentRepository
from app.db.session import Database
from app.domain.enums import ConnectTicketPurpose, PortalAccountStatus
from app.main_web import create_app
from app.security.tokens import generate_token
from app.web.security import CONTENT_SECURITY_POLICY, CSRF_COOKIE_NAME
from tests.support.factories import make_account, make_patient
from tests.support.portal import VALID_EMAIL, VALID_PASSWORD, FakePortal

BASE_URL = "https://bot.test.invalid"


@pytest.fixture
async def client(container: Container) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(container)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        yield client


async def issue_ticket(
    session: AsyncSession,
    user: TelegramUser,
    *,
    purpose: ConnectTicketPurpose = ConnectTicketPurpose.CONNECT,
    account_id: object | None = None,
) -> str:
    token = generate_token()
    await ConnectTicketRepository(session).create(
        user_id=user.id,
        token=token,
        purpose=purpose,
        ttl_minutes=10,
        portal_account_id=account_id,  # type: ignore[arg-type]
    )
    await session.commit()
    return token


# --- health ------------------------------------------------------------------
async def test_liveness_does_not_touch_the_database(client: httpx.AsyncClient) -> None:
    response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "alive"


async def test_readiness_reports_the_schema_revision(
    client: httpx.AsyncClient, clean_session: AsyncSession
) -> None:
    response = await client.get("/health/ready")
    payload = response.json()
    assert payload["database"] is True


async def test_version_is_exposed(client: httpx.AsyncClient) -> None:
    payload = (await client.get("/version")).json()
    assert payload["version"]
    assert payload["schema"]


async def test_security_headers_are_always_present(client: httpx.AsyncClient) -> None:
    response = await client.get("/health/live")
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Content-Security-Policy"] == CONTENT_SECURITY_POLICY
    assert "no-store" in response.headers["Cache-Control"]


async def test_the_landing_page_reveals_nothing(client: httpx.AsyncClient) -> None:
    response = await client.get("/")
    assert response.status_code == 200
    assert "privato" in response.text


# --- connect form ------------------------------------------------------------
async def test_the_form_is_rendered_for_a_valid_ticket(
    client: httpx.AsyncClient, clean_session: AsyncSession, user: TelegramUser
) -> None:
    token = await issue_ticket(clean_session, user)
    response = await client.get(f"/connect/{token}")
    assert response.status_code == 200
    assert 'name="email"' in response.text
    assert 'name="password"' in response.text
    assert CSRF_COOKIE_NAME in response.cookies


async def test_the_form_loads_no_remote_asset(
    client: httpx.AsyncClient, clean_session: AsyncSession, user: TelegramUser
) -> None:
    token = await issue_ticket(clean_session, user)
    body = (await client.get(f"/connect/{token}")).text
    assert "https://" not in body.split("<style>")[0].replace(BASE_URL, "")
    assert "<script" not in body
    assert "googleapis" not in body
    assert "cdn" not in body.lower()


async def test_an_unknown_ticket_is_refused(client: httpx.AsyncClient) -> None:
    response = await client.get("/connect/token-inesistente")
    assert response.status_code == 404
    assert "non è più valido" in response.text


async def test_an_expired_ticket_is_refused(
    client: httpx.AsyncClient, clean_session: AsyncSession, user: TelegramUser
) -> None:
    token = await issue_ticket(clean_session, user)
    ticket = await ConnectTicketRepository(clean_session).get_by_token(token)
    assert ticket is not None
    ticket.expires_at = utc_now().replace(year=2020)
    await clean_session.commit()
    assert (await client.get(f"/connect/{token}")).status_code == 404


async def test_a_consumed_ticket_is_refused(
    client: httpx.AsyncClient, clean_session: AsyncSession, user: TelegramUser
) -> None:
    token = await issue_ticket(clean_session, user)
    ticket = await ConnectTicketRepository(clean_session).get_by_token(token)
    assert ticket is not None
    await ConnectTicketRepository(clean_session).consume(ticket)
    await clean_session.commit()
    assert (await client.get(f"/connect/{token}")).status_code == 404


async def test_a_valid_submission_creates_the_account(
    client: httpx.AsyncClient, clean_session: AsyncSession, user: TelegramUser
) -> None:
    token = await issue_ticket(clean_session, user)
    page = await client.get(f"/connect/{token}")
    csrf = page.cookies[CSRF_COOKIE_NAME]
    response = await client.post(
        f"/connect/{token}",
        data={
            "csrf_token": csrf,
            "label": "Account web",
            "email": VALID_EMAIL,
            "password": VALID_PASSWORD,
        },
        headers={"origin": BASE_URL},
    )
    assert response.status_code == 200
    assert "Account collegato" in response.text
    assert VALID_PASSWORD not in response.text

    accounts = await PortalAccountRepository(clean_session).list_for_user(user.id)
    assert len(accounts) == 1
    assert accounts[0].status is PortalAccountStatus.ACTIVE


async def test_a_wrong_password_yields_a_generic_message(
    client: httpx.AsyncClient, clean_session: AsyncSession, user: TelegramUser
) -> None:
    token = await issue_ticket(clean_session, user)
    csrf = (await client.get(f"/connect/{token}")).cookies[CSRF_COOKIE_NAME]
    response = await client.post(
        f"/connect/{token}",
        data={
            "csrf_token": csrf,
            "label": "Account web",
            "email": VALID_EMAIL,
            "password": "password-sbagliata",
        },
        headers={"origin": BASE_URL},
    )
    assert response.status_code == 400
    assert "Credenziali non valide oppure account non disponibile" in response.text


async def test_an_unknown_account_yields_the_same_message(
    client: httpx.AsyncClient, clean_session: AsyncSession, user: TelegramUser
) -> None:
    """The form must not allow account enumeration."""
    token = await issue_ticket(clean_session, user)
    csrf = (await client.get(f"/connect/{token}")).cookies[CSRF_COOKIE_NAME]
    wrong_account = await client.post(
        f"/connect/{token}",
        data={
            "csrf_token": csrf,
            "label": "Account web",
            "email": "sconosciuto@example.invalid",
            "password": VALID_PASSWORD,
        },
        headers={"origin": BASE_URL},
    )
    assert "Credenziali non valide oppure account non disponibile" in wrong_account.text


async def test_a_missing_csrf_token_is_refused(
    client: httpx.AsyncClient, clean_session: AsyncSession, user: TelegramUser
) -> None:
    token = await issue_ticket(clean_session, user)
    await client.get(f"/connect/{token}")
    response = await client.post(
        f"/connect/{token}",
        data={"label": "x", "email": VALID_EMAIL, "password": VALID_PASSWORD},
        headers={"origin": BASE_URL},
    )
    assert response.status_code == 403
    assert await PortalAccountRepository(clean_session).count_for_user(user.id) == 0


async def test_a_foreign_csrf_token_is_refused(
    client: httpx.AsyncClient, clean_session: AsyncSession, user: TelegramUser
) -> None:
    token = await issue_ticket(clean_session, user)
    await client.get(f"/connect/{token}")
    response = await client.post(
        f"/connect/{token}",
        data={
            "csrf_token": "token-falso",
            "label": "x",
            "email": VALID_EMAIL,
            "password": VALID_PASSWORD,
        },
        headers={"origin": BASE_URL},
    )
    assert response.status_code == 403


async def test_a_foreign_origin_is_refused(
    client: httpx.AsyncClient, clean_session: AsyncSession, user: TelegramUser
) -> None:
    token = await issue_ticket(clean_session, user)
    csrf = (await client.get(f"/connect/{token}")).cookies[CSRF_COOKIE_NAME]
    response = await client.post(
        f"/connect/{token}",
        data={
            "csrf_token": csrf,
            "label": "x",
            "email": VALID_EMAIL,
            "password": VALID_PASSWORD,
        },
        headers={"origin": "https://evil.example"},
    )
    assert response.status_code == 403


async def test_the_ticket_cannot_be_replayed(
    client: httpx.AsyncClient, clean_session: AsyncSession, user: TelegramUser
) -> None:
    token = await issue_ticket(clean_session, user)
    csrf = (await client.get(f"/connect/{token}")).cookies[CSRF_COOKIE_NAME]
    payload = {
        "csrf_token": csrf,
        "label": "Account web",
        "email": VALID_EMAIL,
        "password": VALID_PASSWORD,
    }
    first = await client.post(f"/connect/{token}", data=payload, headers={"origin": BASE_URL})
    assert first.status_code == 200
    second = await client.post(f"/connect/{token}", data=payload, headers={"origin": BASE_URL})
    assert second.status_code == 404
    assert await PortalAccountRepository(clean_session).count_for_user(user.id) == 1


async def test_reauthentication_rotates_the_existing_account(
    client: httpx.AsyncClient,
    clean_session: AsyncSession,
    user: TelegramUser,
    portal: FakePortal,
) -> None:
    account = await make_account(clean_session, user, email="vecchia@example.invalid")
    token = await issue_ticket(
        clean_session,
        user,
        purpose=ConnectTicketPurpose.REAUTH,
        account_id=account.id,
    )
    csrf = (await client.get(f"/connect/{token}")).cookies[CSRF_COOKIE_NAME]
    response = await client.post(
        f"/connect/{token}",
        data={
            "csrf_token": csrf,
            "label": account.label,
            "email": VALID_EMAIL,
            "password": VALID_PASSWORD,
        },
        headers={"origin": BASE_URL},
    )
    assert response.status_code == 200
    assert await PortalAccountRepository(clean_session).count_for_user(user.id) == 1


async def test_an_oversized_body_is_rejected(
    client: httpx.AsyncClient, clean_session: AsyncSession, user: TelegramUser
) -> None:
    token = await issue_ticket(clean_session, user)
    response = await client.post(
        f"/connect/{token}",
        content=b"x" * 40_000,
        headers={"content-type": "application/x-www-form-urlencoded", "origin": BASE_URL},
    )
    assert response.status_code == 413


async def test_repeated_attempts_are_rate_limited(
    client: httpx.AsyncClient,
    clean_session: AsyncSession,
    user: TelegramUser,
    database: Database,
    portal: FakePortal,
) -> None:
    from tests.support.portal import build_container, make_settings

    limited = build_container(
        make_settings(_settings_of(client), connect_rate_limit_per_hour=2),
        database,
        portal,
    )
    app = create_app(limited)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as scoped:
        token = await issue_ticket(clean_session, user)
        statuses = [(await scoped.get(f"/connect/{token}")).status_code for _ in range(4)]
    assert 429 in statuses


def _settings_of(client: httpx.AsyncClient) -> Settings:
    """Read the settings from the ASGI app the client is bound to."""
    transport = client._transport
    return cast(Settings, transport.app.state.settings)  # type: ignore[attr-defined]


# --- PDF download ------------------------------------------------------------
async def test_a_one_time_link_delivers_the_pdf_once(
    client: httpx.AsyncClient,
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
) -> None:
    account = await make_account(clean_session, user)
    await make_patient(clean_session, account)
    await container.appointments.sync_account(clean_session, account)
    appointment = await AppointmentRepository(clean_session).find_by_external_id(
        account.id, "70001"
    )
    assert appointment is not None
    token = generate_token()
    await DownloadTokenRepository(clean_session).create(
        user_id=user.id, appointment_id=appointment.id, token=token, ttl_minutes=10
    )
    await clean_session.commit()

    first = await client.get(f"/download/{token}")
    assert first.status_code == 200
    assert first.headers["content-type"] == "application/pdf"
    assert first.content.startswith(b"%PDF-")
    assert "no-store" in first.headers["Cache-Control"]

    second = await client.get(f"/download/{token}")
    assert second.status_code == 404


async def test_an_unknown_download_token_is_refused(client: httpx.AsyncClient) -> None:
    assert (await client.get("/download/token-inesistente")).status_code == 404


async def test_an_expired_download_token_is_refused(
    client: httpx.AsyncClient,
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
) -> None:
    account = await make_account(clean_session, user)
    await container.appointments.sync_account(clean_session, account)
    appointment = await AppointmentRepository(clean_session).find_by_external_id(
        account.id, "70001"
    )
    assert appointment is not None
    token = generate_token()
    record = await DownloadTokenRepository(clean_session).create(
        user_id=user.id, appointment_id=appointment.id, token=token, ttl_minutes=10
    )
    record.expires_at = utc_now().replace(year=2020)
    await clean_session.commit()
    assert (await client.get(f"/download/{token}")).status_code == 404


async def test_no_administrative_route_is_exposed(client: httpx.AsyncClient) -> None:
    for path in ("/admin", "/api/users", "/docs", "/openapi.json", "/redoc"):
        assert (await client.get(path)).status_code == 404
