"""Portal client contract tests driven by a synthetic HTTP transport."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import httpx
import orjson
import pytest

from app.config import Settings
from app.domain.entities import PortalDoctor, PortalService
from app.domain.errors import (
    AuthenticationFailed,
    PdfUnavailable,
    PortalCaptchaRequired,
    PortalContractChanged,
    PortalRateLimited,
    PortalUnavailable,
)
from app.portal.client import PortalClient, PortalSession
from app.portal.models import PortalCredentials
from app.portal.rate_limit import AsyncRateLimiter, RateLimitPolicy
from tests.fixtures.synthetic import load

SYNTHETIC_EMAIL = "utente.sintetico@example.invalid"
SYNTHETIC_PASSWORD = "synthetic-password-not-real"
PDF_BYTES = b"%PDF-1.7\n synthetic reminder \n%%EOF"


class Recorder:
    """Collects the requests issued by the client for later assertions."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def add(self, request: httpx.Request) -> None:
        self.requests.append(request)

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]

    def count(self, path: str) -> int:
        return sum(1 for request in self.requests if request.url.path == path)

    def last_body(self) -> str:
        return self.requests[-1].content.decode("utf-8")


def build_client(
    settings: Settings,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    recorder: Recorder | None = None,
) -> PortalClient:
    def wrapped(request: httpx.Request) -> httpx.Response:
        if recorder is not None:
            recorder.add(request)
        return handler(request)

    limiter = AsyncRateLimiter(
        RateLimitPolicy(max_concurrency=4, min_interval_seconds=0.0),
        jitter=lambda: 0.0,
        sleep=_no_sleep,
    )
    return PortalClient(
        settings,
        limiter=limiter,
        transport=httpx.MockTransport(wrapped),
        sleep=_no_sleep,
        jitter=lambda: 0.0,
    )


async def _no_sleep(_seconds: float) -> None:
    return None


def html(body: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, text=body, headers={"content-type": "text/html; charset=utf-8"})


def json_response(name: str, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        content=load(name).encode("utf-8"),
        headers={"content-type": "application/json"},
    )


def default_handler(request: httpx.Request) -> httpx.Response:
    """A cooperative portal that answers every documented route."""
    path = request.url.path
    if path == "/login" and request.method == "GET":
        return html(load("login_form.html"))
    if path == "/login":
        return httpx.Response(
            302, headers={"location": "/appointments", "set-cookie": "opbg-prenotazione=synthetic"}
        )
    if path == "/appointments":
        return html(load("appointments_list.html"))
    if path == "/user/info":
        return json_response("user_info.json")
    if path == "/appointments/create" and request.method == "GET":
        return html(load("appointments_create_lapi.html"))
    if path == "/appointments/create":
        body = request.content.decode("utf-8")

        def sent(field: str) -> bool:
            """True when the field carries a value, not merely a name.

            The wizard resubmits its whole form, so every field is present at
            every step and only the filled ones say which step this is.
            """
            return re.search(rf"(^|&){field}=[^&]", body) is not None

        if sent("confirm"):
            return html(load("appointments_confirm.html"))
        if sent("id_proposal"):
            return html(load("appointments_confirm.html"))
        if sent("service") or sent("medico"):
            return html(load("appointments_create_locations.html"))
        if sent("tax_code"):
            return html(load("appointments_create_lapi.html"))
        return html(load("appointments_create_slots.html"))
    if path == "/service-autocomplete":
        return json_response("service_autocomplete.json")
    if path == "/medici-autocomplete":
        return json_response("doctor_autocomplete.json")
    if path == "/appointments/disponibilita":
        return json_response("availability.json")
    if path.endswith("/pdf-download"):
        return httpx.Response(200, content=PDF_BYTES, headers={"content-type": "application/pdf"})
    if path.endswith("/cancel"):
        return httpx.Response(302, headers={"location": "/appointments"})
    if path == "/appointments/history":
        return html(load("appointments_list.html"))
    return httpx.Response(404, text="not found")


@pytest.fixture
async def session_factory(settings: Settings) -> Any:
    async def factory(
        handler: Callable[[httpx.Request], httpx.Response] = default_handler,
        recorder: Recorder | None = None,
    ) -> tuple[PortalClient, PortalSession]:
        client = build_client(settings, handler, recorder=recorder)
        context = client.session(account_key="synthetic-account")
        portal_session = await context.__aenter__()
        portal_session._context = context  # type: ignore[attr-defined]
        return client, portal_session

    return factory


async def close(portal_session: PortalSession) -> None:
    await portal_session.client.aclose()


# --- login -------------------------------------------------------------------
async def test_login_verifies_the_session_with_an_explicit_request(
    settings: Settings,
) -> None:
    recorder = Recorder()
    client = build_client(settings, default_handler, recorder=recorder)
    async with client.session(account_key="a") as portal_session:
        result = await client.login(
            portal_session, PortalCredentials(SYNTHETIC_EMAIL, SYNTHETIC_PASSWORD)
        )
    assert result.verified is True
    assert result.account_metadata == {"id": 4242, "name": "Utente", "surname": "Sintetico"}
    # A redirect alone is not enough: /appointments must have been requested.
    assert "/appointments" in recorder.paths()


async def test_login_copies_the_hidden_fields_of_the_form(settings: Settings) -> None:
    recorder = Recorder()
    client = build_client(settings, default_handler, recorder=recorder)
    async with client.session(account_key="a") as portal_session:
        await client.login(portal_session, PortalCredentials(SYNTHETIC_EMAIL, SYNTHETIC_PASSWORD))
    login_post = next(
        request
        for request in recorder.requests
        if request.url.path == "/login" and request.method == "POST"
    )
    body = login_post.content.decode("utf-8")
    assert "_token=SYNTHETIC-FORM-CSRF-0001" in body
    assert "to_service=lapi" in body


async def test_login_failure_raises_authentication_failed(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login" and request.method == "GET":
            return html(load("login_form.html"))
        if request.url.path == "/login":
            return html(load("login_failed.html"))
        if request.url.path == "/appointments":
            return html(load("login_form.html"))
        return httpx.Response(404)

    client = build_client(settings, handler)
    async with client.session(account_key="a") as portal_session:
        with pytest.raises(AuthenticationFailed):
            await client.login(
                portal_session, PortalCredentials(SYNTHETIC_EMAIL, SYNTHETIC_PASSWORD)
            )


async def test_captcha_challenge_stops_the_flow(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login" and request.method == "GET":
            return html(load("login_form.html"))
        return html(load("captcha_challenge.html"))

    client = build_client(settings, handler)
    async with client.session(account_key="a") as portal_session:
        with pytest.raises(PortalCaptchaRequired):
            await client.login(
                portal_session, PortalCredentials(SYNTHETIC_EMAIL, SYNTHETIC_PASSWORD)
            )


async def test_unauthenticated_status_requires_reauthentication(settings: Settings) -> None:
    client = build_client(settings, lambda _request: httpx.Response(401, text="no"))
    async with client.session(account_key="a") as portal_session:
        assert await client.verify_session(portal_session) is False


# --- retries and error mapping ----------------------------------------------
async def test_server_errors_are_retried_for_idempotent_reads(settings: Settings) -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/appointments":
            calls["count"] += 1
            if calls["count"] < 3:
                return httpx.Response(503, text="unavailable")
            return html(load("appointments_list.html"))
        return httpx.Response(404)

    client = build_client(settings, handler)
    async with client.session(account_key="a") as portal_session:
        appointments = await client.list_appointments(portal_session)
    assert calls["count"] == 3
    assert len(appointments) == 2


async def test_repeated_server_errors_surface_as_portal_unavailable(
    settings: Settings,
) -> None:
    client = build_client(settings, lambda _request: httpx.Response(502, text="bad gateway"))
    async with client.session(account_key="a") as portal_session:
        with pytest.raises(PortalUnavailable):
            await client.list_appointments(portal_session)


async def test_rate_limited_response_is_typed(settings: Settings) -> None:
    client = build_client(
        settings,
        lambda _request: httpx.Response(429, text="slow down", headers={"Retry-After": "1"}),
    )
    async with client.session(account_key="a") as portal_session:
        with pytest.raises(PortalRateLimited):
            await client.list_appointments(portal_session)


async def test_network_failures_map_to_portal_unavailable(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("synthetic timeout", request=request)

    client = build_client(settings, handler)
    async with client.session(account_key="a") as portal_session:
        with pytest.raises(PortalUnavailable):
            await client.list_appointments(portal_session)


async def test_missing_route_is_reported_as_a_contract_change(settings: Settings) -> None:
    client = build_client(settings, lambda _request: httpx.Response(404, text="gone"))
    async with client.session(account_key="a") as portal_session:
        with pytest.raises(PortalContractChanged):
            await client.list_appointments(portal_session)


async def test_confirmation_is_never_retried(settings: Settings) -> None:
    recorder = Recorder()
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/appointments/create" and request.method == "POST":
            attempts["count"] += 1
            return httpx.Response(503, text="unavailable")
        if request.url.path == "/appointments/create":
            return html(load("appointments_create_lapi.html"))
        if request.url.path == "/appointments":
            return html(load("appointments_list.html"))
        return httpx.Response(404)

    client = build_client(settings, handler, recorder=recorder)
    async with client.session(account_key="a") as portal_session:
        portal_session.wizard.html = load("appointments_confirm.html")
        portal_session.csrf_token = "SYNTHETIC-FORM-CSRF-0005"
        with pytest.raises(PortalUnavailable):
            await client.confirm_booking(portal_session)
    assert attempts["count"] == 1


async def test_cancellation_is_never_retried(settings: Settings) -> None:
    """A lost response is indistinguishable from a refusal, so never replay."""
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "/cancel" in request.url.path:
            attempts["count"] += 1
            return httpx.Response(502, text="bad gateway")
        return default_handler(request)

    client = build_client(settings, handler)
    async with client.session(account_key="a") as portal_session:
        with pytest.raises(PortalUnavailable):
            await client.cancel_appointment(
                portal_session,
                appointment_id="9001",
                tax_code="AAABBB01A01A001A",
                cancel_path=None,
            )
    assert attempts["count"] == 1


async def test_a_wizard_step_is_retried_after_a_server_error(settings: Settings) -> None:
    """The portal is flaky; walking the wizard commits nothing, so retry it.

    Choosing a doctor or a service creates no appointment, and giving up on the
    first 500 ends the conversation for a fault that clears by itself.
    """
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode("utf-8") if request.method == "POST" else ""
        if request.method == "POST" and "medico=" in body:
            attempts["count"] += 1
            if attempts["count"] < 3:
                return httpx.Response(500, text="server error")
        return default_handler(request)

    client = build_client(settings, handler)
    async with client.session(account_key="a") as portal_session:
        await client.open_lapi_wizard(portal_session)
        await client.select_patient(portal_session, "AAABBB01A01A001A")
        _, locations = await client.submit_doctor_choice(
            portal_session,
            PortalDoctor(
                raw_value="SINTETICO_UNO",
                description="DOTT. SINTETICO UNO",
                last_name="SINTETICO",
                first_name="UNO",
            ),
        )

    assert attempts["count"] == 3, "the step must be retried, not abandoned"
    assert {item.location_id for item in locations} == {"101", "202"}


# --- wizard ------------------------------------------------------------------
async def test_wizard_reads_patients_and_selects_one(settings: Settings) -> None:
    recorder = Recorder()
    client = build_client(settings, default_handler, recorder=recorder)
    async with client.session(account_key="a") as portal_session:
        patients = await client.open_lapi_wizard(portal_session)
        assert [patient.tax_code for patient in patients] == [
            "AAABBB01A01A001A",
            "CCCDDD02B02B002B",
        ]
        await client.select_patient(portal_session, "AAABBB01A01A001A")
    body = recorder.last_body()
    assert "tax_code=AAABBB01A01A001A" in body
    assert "privacy=1" in body


async def test_service_choice_sends_the_documented_fields(settings: Settings) -> None:
    recorder = Recorder()
    client = build_client(settings, default_handler, recorder=recorder)
    async with client.session(account_key="a") as portal_session:
        await client.open_lapi_wizard(portal_session)
        await client.select_patient(portal_session, "AAABBB01A01A001A")
        _, locations = await client.submit_service_choice(
            portal_session,
            PortalService(
                service_id="9001",
                description="VISITA SINTETICA",
                online_bookable=True,
                specification_id=None,
            ),
        )
    body = recorder.last_body()
    assert "servicetype=lapi" in body
    assert "service=9001" in body
    assert "service_name=VISITA" in body
    assert {location.location_id for location in locations} == {"101", "202"}


async def test_doctor_choice_sends_the_medico_field(settings: Settings) -> None:
    recorder = Recorder()
    client = build_client(settings, default_handler, recorder=recorder)
    async with client.session(account_key="a") as portal_session:
        await client.open_lapi_wizard(portal_session)
        await client.select_patient(portal_session, "AAABBB01A01A001A")
        await client.submit_doctor_choice(
            portal_session,
            PortalDoctor(
                raw_value="DOTT. SINTETICO UNO",
                description="DOTT. SINTETICO UNO",
                last_name="SINTETICO",
                first_name="UNO",
            ),
        )
    body = recorder.last_body()
    assert "medico=DOTT" in body
    assert "servicetype=lapi" in body


async def test_availability_request_uses_the_csrf_header(settings: Settings) -> None:
    recorder = Recorder()
    client = build_client(settings, default_handler, recorder=recorder)
    async with client.session(account_key="a") as portal_session:
        await client.open_lapi_wizard(portal_session)
        availability = await client.fetch_availability(
            portal_session, service_id="9001", location_ids=["101", "202"]
        )
    request = next(
        item for item in recorder.requests if item.url.path == "/appointments/disponibilita"
    )
    assert request.headers["X-CSRF-TOKEN"]
    body = request.content.decode("utf-8")
    assert "idPrestazione=9001" in body
    assert body.count("idUnitaErogante") == 2
    assert len(availability.slots) == 3


async def test_availability_falls_back_to_the_rendered_page(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/appointments/disponibilita":
            return httpx.Response(
                200,
                content=b"<html><body>non json</body></html>",
                headers={"content-type": "application/json"},
            )
        return default_handler(request)

    client = build_client(settings, handler)
    async with client.session(account_key="a") as portal_session:
        portal_session.wizard.html = load("availability_rendered.html")
        availability = await client.fetch_availability(portal_session, service_id="9001")
    assert {slot.proposal_code for slot in availability.slots} == {"PROP-R001", "PROP-R002"}


# --- appointments ------------------------------------------------------------
async def test_appointment_history_absence_is_tolerated(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/appointments/history":
            return httpx.Response(404, text="missing")
        return default_handler(request)

    client = build_client(settings, handler)
    async with client.session(account_key="a") as portal_session:
        assert await client.list_appointment_history(portal_session) == ()


async def test_cancellation_sends_the_tax_code_once(settings: Settings) -> None:
    recorder = Recorder()
    client = build_client(settings, default_handler, recorder=recorder)
    async with client.session(account_key="a") as portal_session:
        await client.cancel_appointment(
            portal_session,
            appointment_id="70001",
            tax_code="AAABBB01A01A001A",
            cancel_path="/appointments/70001/cancel",
        )
    assert recorder.count("/appointments/70001/cancel") == 1


# --- PDF ---------------------------------------------------------------------
async def test_pdf_is_validated(settings: Settings) -> None:
    client = build_client(settings, default_handler)
    async with client.session(account_key="a") as portal_session:
        document = await client.download_pdf(
            portal_session, appointment_id="70001", max_bytes=1_000_000
        )
    assert document.content.startswith(b"%PDF-")
    assert document.filename == "promemoria-70001.pdf"


async def test_pdf_with_the_wrong_content_type_is_rejected(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=PDF_BYTES, headers={"content-type": "text/html"})

    client = build_client(settings, handler)
    async with client.session(account_key="a") as portal_session:
        with pytest.raises(PdfUnavailable):
            await client.download_pdf(portal_session, appointment_id="70001", max_bytes=1_000_000)


async def test_pdf_without_magic_bytes_is_rejected(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"<html>not a pdf</html>", headers={"content-type": "application/pdf"}
        )

    client = build_client(settings, handler)
    async with client.session(account_key="a") as portal_session:
        with pytest.raises(PdfUnavailable):
            await client.download_pdf(portal_session, appointment_id="70001", max_bytes=1_000_000)


async def test_pdf_over_the_size_limit_is_rejected(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"%PDF-" + b"x" * 5_000,
            headers={"content-type": "application/pdf"},
        )

    client = build_client(settings, handler)
    async with client.session(account_key="a") as portal_session:
        with pytest.raises(PdfUnavailable):
            await client.download_pdf(portal_session, appointment_id="70001", max_bytes=1_000)


async def test_pdf_filename_is_sanitised(settings: Settings) -> None:
    client = build_client(settings, default_handler)
    async with client.session(account_key="a") as portal_session:
        document = await client.download_pdf(
            portal_session, appointment_id="../../etc/passwd", max_bytes=1_000_000
        )
    assert "/" not in document.filename
    assert document.filename == "promemoria-etcpasswd.pdf"


# --- session isolation -------------------------------------------------------
async def test_cookies_are_not_shared_between_sessions(settings: Settings) -> None:
    client = build_client(settings, default_handler)
    async with (
        client.session(account_key="a", cookies={"opbg-prenotazione": "first"}) as first,
        client.session(account_key="b") as second,
    ):
        assert first.cookie_names() == {"opbg-prenotazione"}
        assert second.cookie_names() == set()


# --- cookie jar --------------------------------------------------------------
async def test_the_same_cookie_name_on_two_paths_is_kept(settings: Settings) -> None:
    """The portal sets XSRF-TOKEN on more than one path.

    Exporting such a jar as a name/value mapping raises ``CookieConflict``, so
    the export keeps every cookie with the scope it was set for.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        response = default_handler(request)
        # Two Set-Cookie headers with one name, as the portal actually sends.
        response.headers = httpx.Headers(
            [
                *response.headers.multi_items(),
                ("set-cookie", "XSRF-TOKEN=root; Path=/"),
                ("set-cookie", "XSRF-TOKEN=scoped; Path=/appointments"),
            ]
        )
        return response

    client = build_client(settings, handler)
    async with client.session(account_key="a") as portal_session:
        await client.verify_session(portal_session)
        exported = portal_session.export_cookies()

    tokens = [record for record in exported if record["name"] == "XSRF-TOKEN"]
    assert len(tokens) == 2
    assert {record["value"] for record in tokens} == {"root", "scoped"}
    assert {record["path"] for record in tokens} == {"/", "/appointments"}


async def test_an_exported_jar_can_be_loaded_back(settings: Settings) -> None:
    """A jar must survive a full round trip through storage."""
    stored = [
        {"name": "XSRF-TOKEN", "value": "root", "domain": "portal.test.invalid", "path": "/"},
        {
            "name": "XSRF-TOKEN",
            "value": "scoped",
            "domain": "portal.test.invalid",
            "path": "/appointments",
        },
        {
            "name": "opbg-prenotazione",
            "value": "session",
            "domain": "portal.test.invalid",
            "path": "/",
        },
    ]
    client = build_client(settings, default_handler)
    async with client.session(account_key="a", cookies=stored) as portal_session:
        exported = portal_session.export_cookies()
        assert portal_session.cookie_names() == {"XSRF-TOKEN", "opbg-prenotazione"}

    # The jar does not promise an order, so compare the records themselves.
    key = lambda record: (record["name"], record["path"])  # noqa: E731
    assert sorted(exported, key=key) == sorted(stored, key=key)


async def test_a_jar_stored_in_the_old_flat_format_is_still_loaded(settings: Settings) -> None:
    """An account connected before the jar carried scopes must keep working."""
    client = build_client(settings, default_handler)
    async with client.session(
        account_key="a", cookies={"opbg-prenotazione": "session"}
    ) as portal_session:
        exported = portal_session.export_cookies()

    assert [record["name"] for record in exported] == ["opbg-prenotazione"]
    assert exported[0]["value"] == "session"


async def test_contract_change_on_json_endpoint_is_typed(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/service-autocomplete":
            return httpx.Response(
                200, content=b"<html>oops</html>", headers={"content-type": "application/json"}
            )
        return default_handler(request)

    client = build_client(settings, handler)
    async with client.session(account_key="a") as portal_session:
        with pytest.raises(PortalContractChanged):
            await client.autocomplete_services(
                portal_session, query="visita", tax_code="AAABBB01A01A001A"
            )


def test_response_description_never_contains_the_body(settings: Settings) -> None:
    response = httpx.Response(
        200,
        text=load("appointments_list.html"),
        headers={"content-type": "text/html"},
        request=httpx.Request("GET", "https://portal.test.invalid/appointments"),
    )
    described = PortalClient.describe(response)
    payload = orjson.dumps(described.as_context()).decode("utf-8")
    assert "VISITA SINTETICA" not in payload
    assert described.body_length > 0


def _unused(value: Any) -> None:  # pragma: no cover - keeps type checkers quiet
    return None
