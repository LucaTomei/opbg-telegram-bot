"""HTTP adapter for the OPBG booking portal.

The portal is an unstable HTML integration: every request goes through this
module, which owns the cookie jar, the CSRF handling, the retry policy and the
translation of transport failures into typed domain errors.  Nothing outside
this package may issue portal requests, and no raw response body ever leaves it.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any, Final

import httpx
import orjson

from app.config import Settings
from app.domain.entities import (
    LoginResult,
    PdfDocument,
    PortalAppointment,
    PortalAvailability,
    PortalDoctor,
    PortalLocation,
    PortalPatient,
    PortalService,
    PortalSlot,
)
from app.domain.errors import (
    AuthenticationFailed,
    PdfUnavailable,
    PortalCaptchaRequired,
    PortalContractChanged,
    PortalRateLimited,
    PortalUnavailable,
    ReauthenticationRequired,
)
from app.logging import get_logger
from app.portal import parsers
from app.portal.forms import (
    HtmlForm,
    extract_csrf_token,
    find_csrf_token,
    find_form,
    parse_document,
)
from app.portal.models import PortalCredentials, PortalResponse, WizardState
from app.portal.rate_limit import AsyncRateLimiter, NullPortalGate, PortalGate

logger = get_logger(__name__)

LOGIN_PATH: Final[str] = "/login"
USER_INFO_PATH: Final[str] = "/user/info"
APPOINTMENTS_PATH: Final[str] = "/appointments"
APPOINTMENTS_HISTORY_PATH: Final[str] = "/appointments/history"
CREATE_PATH: Final[str] = "/appointments/create"
AVAILABILITY_PATH: Final[str] = "/appointments/disponibilita"
SERVICE_AUTOCOMPLETE_PATH: Final[str] = "/service-autocomplete"
DOCTOR_AUTOCOMPLETE_PATH: Final[str] = "/medici-autocomplete"

PDF_MAGIC: Final[bytes] = b"%PDF-"
_RETRYABLE_STATUS: Final[frozenset[int]] = frozenset({500, 502, 503, 504})


class PortalSession:
    """One authenticated (or anonymous) portal session bound to one account."""

    def __init__(self, client: httpx.AsyncClient, account_key: str) -> None:
        self._client = client
        self.account_key = account_key
        self.csrf_token: str = ""
        self.wizard = WizardState()

    @property
    def client(self) -> httpx.AsyncClient:
        return self._client

    def cookies_as_dict(self) -> dict[str, str]:
        return dict(self._client.cookies.items())

    def load_cookies(self, cookies: Mapping[str, str] | None) -> None:
        if not cookies:
            return
        for name, value in cookies.items():
            self._client.cookies.set(name, value)

    def clear_cookies(self) -> None:
        self._client.cookies.clear()


class PortalClient:
    """High level portal operations."""

    def __init__(
        self,
        settings: Settings,
        *,
        limiter: AsyncRateLimiter,
        gate: PortalGate | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Any] | None = None,
        jitter: Callable[[], float] | None = None,
    ) -> None:
        self._settings = settings
        self._limiter = limiter
        self._gate = gate or NullPortalGate()
        self._transport = transport
        self._sleep = sleep or asyncio.sleep
        self._jitter = jitter or (lambda: random.uniform(0.0, 0.4))

    # --- session management ---------------------------------------------
    @asynccontextmanager
    async def session(
        self, *, account_key: str, cookies: Mapping[str, str] | None = None
    ) -> AsyncIterator[PortalSession]:
        """Open an isolated session; cookies are never shared between accounts."""
        client = httpx.AsyncClient(
            base_url=self._settings.portal_base_url,
            timeout=httpx.Timeout(self._settings.portal_timeout_seconds),
            follow_redirects=True,
            headers={
                "User-Agent": self._settings.portal_user_agent,
                "Accept-Language": "it-IT,it;q=0.9",
            },
            transport=self._transport,
            cookies=None,
        )
        session = PortalSession(client, account_key)
        session.load_cookies(cookies)
        try:
            yield session
        finally:
            await client.aclose()

    # --- transport -------------------------------------------------------
    async def _request(
        self,
        session: PortalSession,
        method: str,
        path: str,
        *,
        operation: str,
        data: Any = None,
        headers: Mapping[str, str] | None = None,
        allow_retry: bool = True,
        expect_json: bool = False,
    ) -> httpx.Response:
        """Issue one portal request with rate limiting, retries and error mapping.

        ``allow_retry`` must be ``False`` for every mutating request: a booking
        confirmation or a cancellation is never replayed automatically.
        """
        attempts = self._settings.portal_max_retries + 1 if allow_retry else 1
        last_error: Exception | None = None

        for attempt in range(attempts):
            await self._gate.acquire(operation=operation, account_key=session.account_key)
            async with self._limiter.slot(account_key=session.account_key):
                try:
                    response = await session.client.request(
                        method,
                        path,
                        data=data,
                        headers=dict(headers or {}),
                    )
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last_error = PortalUnavailable("portal transport failure", operation=operation)
                    logger.warning(
                        "portal request failed at the transport layer",
                        extra={
                            "event_code": "PORTAL_TRANSPORT_ERROR",
                            "operation": operation,
                            "attempt": attempt + 1,
                            "error_type": type(exc).__name__,
                        },
                    )
                else:
                    outcome = self._classify(response, operation=operation, expect_json=expect_json)
                    if outcome is None:
                        return response
                    last_error = outcome
                    if not isinstance(outcome, PortalUnavailable | PortalRateLimited):
                        raise outcome

            if attempt + 1 < attempts:
                await self._sleep(self._backoff_delay(attempt, last_error))

        assert last_error is not None
        raise last_error

    def _backoff_delay(self, attempt: int, error: Exception | None) -> float:
        if isinstance(error, PortalRateLimited) and error.retry_after_seconds:
            return float(min(error.retry_after_seconds, self._settings.portal_backoff_max_seconds))
        base: float = self._settings.portal_backoff_base_seconds * (2**attempt)
        capped: float = min(base, self._settings.portal_backoff_max_seconds)
        return capped + self._jitter()

    def _classify(
        self, response: httpx.Response, *, operation: str, expect_json: bool
    ) -> Exception | None:
        """Map an HTTP response to a domain error, or ``None`` when acceptable."""
        status = response.status_code
        if status == 429:
            retry_after = response.headers.get("Retry-After")
            seconds = float(retry_after) if retry_after and retry_after.isdigit() else 0.0
            return PortalRateLimited(
                "portal responded with a throttling status",
                retry_after_seconds=seconds,
                operation=operation,
            )
        if status in _RETRYABLE_STATUS:
            return PortalUnavailable("portal responded with a server error", operation=operation)
        if status in {401, 403, 419}:
            return ReauthenticationRequired(
                "portal rejected the current session", operation=operation
            )
        if status == 404:
            return PortalContractChanged("portal route not found", operation=operation)
        if status >= 400:
            return PortalUnavailable("unexpected portal status", operation=operation)

        content_type = response.headers.get("content-type", "")
        if not expect_json and "text/html" in content_type:
            challenge = parsers.detect_portal_challenge(response.text)
            if challenge:
                return PortalCaptchaRequired(
                    "portal requires a human challenge", challenge=challenge
                )
        return None

    @staticmethod
    def describe(response: httpx.Response) -> PortalResponse:
        """Build a redacted description of a response for diagnostics."""
        content_type = response.headers.get("content-type", "")
        challenge = None
        messages: tuple[str, ...] = ()
        if "text/html" in content_type:
            challenge = parsers.detect_portal_challenge(response.text)
            messages = parsers.parse_error_messages(response.text)
        return PortalResponse(
            status_code=response.status_code,
            url=str(response.url.path),
            content_type=content_type,
            body_length=len(response.content),
            challenge=challenge,
            messages=messages,
        )

    @staticmethod
    def _json(response: httpx.Response, *, operation: str) -> Any:
        try:
            return orjson.loads(response.content)
        except orjson.JSONDecodeError as exc:
            raise PortalContractChanged(
                "portal returned a non JSON payload", operation=operation
            ) from exc

    def _remember_csrf(self, session: PortalSession, html: str) -> str:
        token = find_csrf_token(parse_document(html))
        if token:
            session.csrf_token = token
        return session.csrf_token

    # --- authentication --------------------------------------------------
    async def fetch_login_form(self, session: PortalSession) -> HtmlForm:
        response = await self._request(session, "GET", LOGIN_PATH, operation="login_form")
        form = parsers.parse_login_form(response.text)
        session.csrf_token = form.fields.get("_token", "") or self._remember_csrf(
            session, response.text
        )
        return form

    async def login(self, session: PortalSession, credentials: PortalCredentials) -> LoginResult:
        """Authenticate and verify the resulting session.

        A ``302`` alone is never treated as success: the session is always
        verified with an explicit authenticated request.
        """
        form = await self.fetch_login_form(session)
        payload = form.payload(
            {
                "email": credentials.email,
                "password": credentials.password,
            }
        )
        response = await self._request(
            session,
            "POST",
            form.action or LOGIN_PATH,
            operation="login",
            data=payload,
            allow_retry=False,
        )
        challenge = parsers.detect_portal_challenge(response.text)
        if challenge:
            raise PortalCaptchaRequired("portal requires a human challenge", challenge=challenge)

        verified = await self.verify_session(session)
        if not verified:
            raise AuthenticationFailed("portal login could not be verified")

        metadata = await self.fetch_user_info(session)
        return LoginResult(
            verified=True, cookies=session.cookies_as_dict(), account_metadata=metadata
        )

    async def verify_session(self, session: PortalSession) -> bool:
        """Confirm the session is authenticated using an explicit request."""
        try:
            response = await self._request(
                session, "GET", APPOINTMENTS_PATH, operation="verify_session"
            )
        except ReauthenticationRequired:
            return False
        if not parsers.looks_authenticated(response.text):
            return False
        self._remember_csrf(session, response.text)
        return True

    async def fetch_user_info(self, session: PortalSession) -> dict[str, Any]:
        """Read the non-sensitive part of ``/user/info``.

        Access and refresh tokens present in the payload are discarded by the
        parser and are never stored or logged.
        """
        try:
            response = await self._request(
                session, "GET", USER_INFO_PATH, operation="user_info", expect_json=True
            )
        except (PortalContractChanged, PortalUnavailable):
            return {}
        try:
            return parsers.parse_user_info(self._json(response, operation="user_info"))
        except PortalContractChanged:
            return {}

    # --- LAPI wizard -----------------------------------------------------
    async def open_lapi_wizard(self, session: PortalSession) -> tuple[PortalPatient, ...]:
        """Open the LAPI booking wizard and read the available patients."""
        response = await self._request(
            session,
            "GET",
            f"{CREATE_PATH}?service_type=lapi",
            operation="open_wizard",
        )
        html = response.text
        if not parsers.looks_authenticated(html):
            raise ReauthenticationRequired("portal wizard requires a fresh login")
        patients = parsers.parse_patients(html)
        session.wizard = WizardState(
            html=html,
            csrf_token=self._remember_csrf(session, html),
            patients=patients,
            stage="WIZARD_OPEN",
        )
        return patients

    async def select_patient(self, session: PortalSession, tax_code: str) -> str:
        """Submit the patient selection using the values rendered by the portal."""
        if session.wizard.stage == "INITIAL":
            await self.open_lapi_wizard(session)
        document = parse_document(session.wizard.html)
        form = find_form(document, selector_id="appointment-form") or find_form(
            document, action_contains=CREATE_PATH
        )
        if form is None:
            raise PortalContractChanged("patient selection form not found")
        form.require("_token", parsers.PATIENT_SELECT_NAME)
        overrides = {"_token": session.csrf_token or form.fields["_token"], "tax_code": tax_code}
        for consent_field in ("privacy", "newsletter"):
            if consent_field in form.fields:
                current = form.fields[consent_field]
                overrides[consent_field] = current if current else "1"
            elif consent_field == "privacy":
                # The portal requires an explicit privacy acknowledgement; the
                # user accepted it in Telegram before reaching this stage.
                overrides[consent_field] = "1"
        if "newsletter" not in overrides:
            overrides["newsletter"] = form.fields.get("newsletter", "0") or "0"

        response = await self._request(
            session,
            "POST",
            form.action or CREATE_PATH,
            operation="select_patient",
            data=form.payload(overrides),
            allow_retry=False,
        )
        html = response.text
        session.wizard = WizardState(
            html=html,
            csrf_token=self._remember_csrf(session, html),
            patients=session.wizard.patients,
            selected_tax_code=tax_code,
            session_fields=parsers.parse_session_service_fields(html),
            stage="PATIENT_SELECTED",
        )
        return html

    async def autocomplete_services(
        self, session: PortalSession, *, query: str, tax_code: str
    ) -> tuple[PortalService, ...]:
        response = await self._request(
            session,
            "POST",
            SERVICE_AUTOCOMPLETE_PATH,
            operation="service_autocomplete",
            data={
                "_token": session.csrf_token,
                "service": query,
                "tax_code": tax_code,
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
            expect_json=True,
        )
        return parsers.parse_services(self._json(response, operation="service_autocomplete"))

    async def autocomplete_doctors(
        self, session: PortalSession, *, query: str, tax_code: str
    ) -> tuple[PortalDoctor, ...]:
        response = await self._request(
            session,
            "POST",
            DOCTOR_AUTOCOMPLETE_PATH,
            operation="doctor_autocomplete",
            data={
                "_token": session.csrf_token,
                "service": query,
                "tax_code": tax_code,
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
            expect_json=True,
        )
        return parsers.parse_doctors(self._json(response, operation="doctor_autocomplete"))

    async def submit_service_choice(
        self, session: PortalSession, service: PortalService
    ) -> tuple[str, tuple[PortalLocation, ...]]:
        """Submit the exact service selected by the user."""
        payload = {
            "_token": session.csrf_token,
            "id_specification": service.specification_id or "",
            "servicetype": "lapi",
            "service": service.service_id,
            "service_name": service.description,
        }
        payload.update(
            {
                key: value
                for key, value in session.wizard.session_fields.items()
                if key not in payload
            }
        )
        response = await self._request(
            session,
            "POST",
            CREATE_PATH,
            operation="submit_service",
            data=payload,
            allow_retry=False,
        )
        return self._absorb_search_step(session, response.text)

    async def submit_doctor_choice(
        self, session: PortalSession, doctor: PortalDoctor, *, specification_id: str = ""
    ) -> tuple[str, tuple[PortalLocation, ...]]:
        """Submit the exact doctor selected by the user."""
        payload = {
            "_token": session.csrf_token,
            "id_specification": specification_id,
            "servicetype": "lapi",
            "medico": doctor.raw_value,
        }
        payload.update(
            {
                key: value
                for key, value in session.wizard.session_fields.items()
                if key not in payload
            }
        )
        response = await self._request(
            session,
            "POST",
            CREATE_PATH,
            operation="submit_doctor",
            data=payload,
            allow_retry=False,
        )
        return self._absorb_search_step(session, response.text)

    def _absorb_search_step(
        self, session: PortalSession, html: str
    ) -> tuple[str, tuple[PortalLocation, ...]]:
        locations = parsers.parse_location_options(html)
        session.wizard = WizardState(
            html=html,
            csrf_token=self._remember_csrf(session, html),
            patients=session.wizard.patients,
            locations=locations,
            selected_tax_code=session.wizard.selected_tax_code,
            session_fields=parsers.parse_session_service_fields(html),
            stage="SEARCH_SUBMITTED",
        )
        return html, locations

    async def fetch_availability(
        self,
        session: PortalSession,
        *,
        service_id: str,
        location_ids: Sequence[str] = (),
    ) -> PortalAvailability:
        """Read availability from the JSON endpoint, falling back to the HTML step."""
        payload: list[tuple[str, str]] = [("idPrestazione", str(service_id))]
        payload.extend(("idUnitaErogante[]", str(value)) for value in location_ids)
        headers = {
            "X-CSRF-TOKEN": session.csrf_token,
            "X-Requested-With": "XMLHttpRequest",
        }
        try:
            response = await self._request(
                session,
                "POST",
                AVAILABILITY_PATH,
                operation="availability",
                data=payload,
                headers=headers,
                expect_json=True,
            )
            return parsers.parse_availability(self._json(response, operation="availability"))
        except PortalContractChanged:
            if not session.wizard.html:
                raise
            rendered = parsers.parse_server_rendered_availability(session.wizard.html)
            if not rendered.slots and not rendered.locations:
                raise
            logger.info(
                "availability resolved from the server rendered step",
                extra={"event_code": "AVAILABILITY_HTML_FALLBACK"},
            )
            return rendered

    async def select_slot(self, session: PortalSession, slot: PortalSlot) -> str:
        """Submit the chosen proposal using the values supplied by the portal."""
        overrides = {
            "_token": session.csrf_token,
            "id_proposal": slot.proposal_code,
            "location_details": slot.form_fields.get("location_details") or slot.location_id,
            "location_name": slot.form_fields.get("location_name") or slot.location_name,
            "selected_date": slot.form_fields.get("selected_date")
            or slot.raw_date_value
            or slot.starts_at_local.strftime("%Y-%m-%d %H:%M"),
            "session_id_prestazione": slot.form_fields.get("session_id_prestazione")
            or session.wizard.session_fields.get("session_id_prestazione")
            or (slot.service_id or ""),
            "session_name_prestazione": slot.form_fields.get("session_name_prestazione")
            or session.wizard.session_fields.get("session_name_prestazione")
            or "",
        }
        try:
            form = parsers.parse_slot_selection_form(session.wizard.html, slot.proposal_code)
            payload = form.payload(overrides)
            action = form.action or CREATE_PATH
        except PortalContractChanged:
            payload = overrides
            action = CREATE_PATH

        response = await self._request(
            session,
            "POST",
            action,
            operation="select_slot",
            data=payload,
            allow_retry=False,
        )
        html = response.text
        session.wizard = WizardState(
            html=html,
            csrf_token=self._remember_csrf(session, html),
            patients=session.wizard.patients,
            locations=session.wizard.locations,
            selected_tax_code=session.wizard.selected_tax_code,
            session_fields=parsers.parse_session_service_fields(html),
            stage="SLOT_SELECTED",
        )
        return html

    async def confirm_booking(self, session: PortalSession) -> PortalResponse:
        """Send the final confirmation exactly once.

        The caller is responsible for holding the account lock and for
        reconciling the appointment list afterwards: a ``200`` response is not
        proof that the booking succeeded.
        """
        form = parsers.parse_confirmation_form(session.wizard.html)
        payload = form.payload({"_token": session.csrf_token, "confirm": "true"})
        response = await self._request(
            session,
            "POST",
            form.action or CREATE_PATH,
            operation="confirm_booking",
            data=payload,
            allow_retry=False,
        )
        session.wizard.stage = "CONFIRM_SENT"
        session.wizard.html = response.text
        return self.describe(response)

    # --- appointments ----------------------------------------------------
    async def list_appointments(self, session: PortalSession) -> tuple[PortalAppointment, ...]:
        response = await self._request(
            session, "GET", APPOINTMENTS_PATH, operation="list_appointments"
        )
        if not parsers.looks_authenticated(response.text):
            raise ReauthenticationRequired("appointment list requires a fresh login")
        self._remember_csrf(session, response.text)
        return parsers.parse_appointments(response.text)

    async def list_appointment_history(
        self, session: PortalSession
    ) -> tuple[PortalAppointment, ...]:
        """Read the optional history page; its absence must not break the flow."""
        try:
            response = await self._request(
                session, "GET", APPOINTMENTS_HISTORY_PATH, operation="list_history"
            )
        except (PortalContractChanged, PortalUnavailable):
            return ()
        try:
            return parsers.parse_appointments(response.text)
        except PortalContractChanged:
            return ()

    async def cancel_appointment(
        self, session: PortalSession, *, appointment_id: str, tax_code: str, cancel_path: str | None
    ) -> PortalResponse:
        """Request a cancellation exactly once; the caller verifies the outcome."""
        path = cancel_path or f"{APPOINTMENTS_PATH}/{appointment_id}/cancel"
        if path.startswith("http"):
            path = httpx.URL(path).raw_path.decode("ascii")
        separator = "&" if "?" in path else "?"
        target = path if "tax_code=" in path else f"{path}{separator}tax_code={tax_code}"
        response = await self._request(
            session,
            "GET",
            target,
            operation="cancel_appointment",
            allow_retry=False,
        )
        return self.describe(response)

    async def download_pdf(
        self, session: PortalSession, *, appointment_id: str, max_bytes: int
    ) -> PdfDocument:
        """Download and validate an appointment reminder PDF."""
        response = await self._request(
            session,
            "GET",
            f"{APPOINTMENTS_PATH}/{appointment_id}/pdf-download",
            operation="download_pdf",
        )
        content_type = response.headers.get("content-type", "").lower()
        if "application/pdf" not in content_type:
            raise PdfUnavailable("portal did not return a PDF", content_type=content_type)
        content = response.content
        if not content.startswith(PDF_MAGIC):
            raise PdfUnavailable("downloaded file is not a valid PDF")
        if len(content) > max_bytes:
            raise PdfUnavailable("PDF exceeds the configured size limit", size=str(len(content)))
        safe_id = "".join(ch for ch in appointment_id if ch.isalnum() or ch in "-_")[:32]
        return PdfDocument(filename=f"promemoria-{safe_id}.pdf", content=content)


__all__ = [
    "PortalClient",
    "PortalSession",
    "extract_csrf_token",
]
