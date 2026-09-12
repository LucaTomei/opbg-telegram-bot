"""HTTP adapter for the OPBG booking portal.

The portal is an unstable HTML integration: every request goes through this
module, which owns the cookie jar, the CSRF handling, the retry policy and the
translation of transport failures into typed domain errors.  Nothing outside
this package may issue portal requests, and no raw response body ever leaves it.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any, Final

import httpx
import orjson

from app.config import Settings
from app.domain.entities import (
    CookieJar,
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
    PortalError,
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

#: The only care pathway this product covers.
LAPI_SERVICE_TYPE: Final[str] = "lapi"

#: Operations that submit a wizard form. The portal takes its time over these,
#: while a JSON endpoint that has not answered in half a minute will not.
SLOW_OPERATIONS: Final[frozenset[str]] = frozenset(
    {
        "login",
        "select_patient",
        "submit_service",
        "submit_doctor",
        "select_slot",
        "confirm_booking",
        "cancel_appointment",
    }
)

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

    def export_cookies(self) -> list[dict[str, str]]:
        """Every cookie in the jar, each with the scope it was set for.

        Reading the jar as a name/value mapping is not possible: the portal
        sets ``XSRF-TOKEN`` on more than one path and ``httpx`` raises
        ``CookieConflict`` when a duplicated name is looked up.
        """
        return [
            {
                "name": cookie.name,
                "value": cookie.value or "",
                "domain": cookie.domain or "",
                "path": cookie.path or "/",
            }
            for cookie in self._client.cookies.jar
        ]

    def cookie_names(self) -> set[str]:
        return {cookie.name for cookie in self._client.cookies.jar}

    def load_cookies(self, cookies: CookieJar | None) -> None:
        if not cookies:
            return
        if isinstance(cookies, Mapping):
            # A jar stored before the scope was kept: load it as it is.
            for name, value in cookies.items():
                self._client.cookies.set(str(name), str(value))
            return
        for record in cookies:
            name = record.get("name") or ""
            if not name:
                continue
            self._client.cookies.set(
                name,
                record.get("value", ""),
                domain=record.get("domain", ""),
                path=record.get("path", "/"),
            )

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
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._settings = settings
        self._limiter = limiter
        self._gate = gate or NullPortalGate()
        self._transport = transport
        self._sleep = sleep or asyncio.sleep
        self._jitter = jitter or (lambda: random.uniform(0.0, 0.4))
        self._clock = clock or time.monotonic

    # --- session management ---------------------------------------------
    @asynccontextmanager
    async def session(
        self, *, account_key: str, cookies: CookieJar | None = None
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

        ``allow_retry`` must be ``False`` for a request that creates or destroys
        an appointment: a booking confirmation or a cancellation is never
        replayed automatically, because a lost response is indistinguishable
        from a refusal.

        Everything else — logging in, walking the wizard, reading availability —
        may be retried. Those steps commit nothing, and the portal is flaky
        enough that a single server error would otherwise end the conversation.
        """
        attempts = self._settings.portal_max_retries + 1 if allow_retry else 1
        last_error: Exception | None = None
        started = self._clock()

        for attempt in range(attempts):
            await self._gate.acquire(operation=operation, account_key=session.account_key)
            async with self._limiter.slot(account_key=session.account_key):
                try:
                    response = await session.client.request(
                        method,
                        path,
                        data=data,
                        headers=dict(headers or {}),
                        timeout=self._timeout_for(operation),
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
                    # Without this the logs show only the final domain error and
                    # give no way to tell a portal fault from a local one.
                    logger.warning(
                        "portal refused the request",
                        extra={
                            "event_code": "PORTAL_HTTP_ERROR",
                            "operation": operation,
                            "attempt": attempt + 1,
                            "status_code": response.status_code,
                            "content_type": response.headers.get("content-type", ""),
                            "error_type": type(outcome).__name__,
                        },
                    )
                    last_error = outcome
                    if not isinstance(outcome, PortalUnavailable | PortalRateLimited):
                        raise outcome

            if attempt + 1 >= attempts:
                break
            delay = self._backoff_delay(attempt, last_error)
            spent = self._clock() - started
            budget = self._settings.portal_operation_budget_seconds
            if spent + delay >= budget:
                logger.warning(
                    "giving up on the portal to keep the wait bounded",
                    extra={
                        "event_code": "PORTAL_BUDGET_EXHAUSTED",
                        "operation": operation,
                        "attempt": attempt + 1,
                        "seconds_spent": round(spent, 1),
                    },
                )
                break
            await self._sleep(delay)

        assert last_error is not None
        raise last_error

    def _timeout_for(self, operation: str) -> float:
        if operation in SLOW_OPERATIONS:
            return self._settings.portal_slow_timeout_seconds
        return self._settings.portal_timeout_seconds

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
        if status == 400:
            # The portal understood and refused: the request is wrong, and
            # repeating it verbatim can only be refused again.
            return PortalContractChanged("portal rejected the request", operation=operation)
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
        )
        challenge = parsers.detect_portal_challenge(response.text)
        if challenge:
            raise PortalCaptchaRequired("portal requires a human challenge", challenge=challenge)

        # Strict on purpose. A portal that cannot answer right now must not be
        # reported as a wrong password: the caller reacts to a failed login by
        # suspending the account and asking a human for credentials, so a
        # passing outage would cost a working account its stored login.
        try:
            verified, page = await self._probe_session(session)
        except ReauthenticationRequired as exc:
            raise AuthenticationFailed("portal refused the session created by the login") from exc
        if not verified:
            if not parsers.looks_like_login_form(page):
                # The portal neither let us in nor asked for credentials. It
                # has changed shape, and calling that a wrong password would
                # suspend every account at once over a site redesign.
                raise PortalContractChanged(
                    "the portal answered the login check with an unknown page"
                )
            raise AuthenticationFailed("portal login could not be verified")

        metadata = await self.fetch_user_info(session)
        return LoginResult(
            verified=True, cookies=session.export_cookies(), account_metadata=metadata
        )

    async def _probe_session(self, session: PortalSession) -> tuple[bool, str]:
        """Ask the portal whether this session is authenticated.

        Returns the verdict together with the page it was read from, so a
        caller can tell why the answer was no. Transport and throttling
        failures escape as themselves, so "not logged in" and "could not
        check" never collapse into the same answer either.
        """
        response = await self._request(
            session, "GET", APPOINTMENTS_PATH, operation="verify_session"
        )
        if not parsers.looks_authenticated(response.text):
            return False, response.text
        self._remember_csrf(session, response.text)
        return True, response.text

    async def verify_session(self, session: PortalSession) -> bool:
        """Confirm the stored session is usable, as a plain yes or no.

        Any portal error means the stored session cannot be trusted, not that
        the operation is over: letting one escape would skip the fresh login
        that is the only way back, and a jar the portal refuses would keep
        failing for ever.  Use :meth:`_probe_session` where the difference
        between a refusal and an outage decides an account's fate.
        """
        try:
            verified, _ = await self._probe_session(session)
        except ReauthenticationRequired:
            return False
        except PortalError as exc:
            logger.info(
                "the stored session could not be verified; logging in again",
                extra={
                    "event_code": "SESSION_VERIFY_FAILED",
                    "error_type": type(exc).__name__,
                },
            )
            return False
        return verified

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

    def _autocomplete_payload(
        self, session: PortalSession, *, query: str, tax_code: str
    ) -> dict[str, str]:
        """The fields the wizard sends to both autocomplete endpoints.

        ``service_type`` is not optional: the service endpoint answers without
        it, the doctor one replies with a server error.
        """
        return {
            "_token": session.csrf_token,
            "service": query,
            "tax_code": tax_code,
            "service_type": session.wizard.session_fields.get("service_type") or LAPI_SERVICE_TYPE,
        }

    async def autocomplete_services(
        self, session: PortalSession, *, query: str, tax_code: str
    ) -> tuple[PortalService, ...]:
        response = await self._request(
            session,
            "POST",
            SERVICE_AUTOCOMPLETE_PATH,
            operation="service_autocomplete",
            data=self._autocomplete_payload(session, query=query, tax_code=tax_code),
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
            data=self._autocomplete_payload(session, query=query, tax_code=tax_code),
            headers={"X-Requested-With": "XMLHttpRequest"},
            expect_json=True,
        )
        return parsers.parse_doctors(self._json(response, operation="doctor_autocomplete"))

    async def submit_service_choice(
        self, session: PortalSession, service: PortalService
    ) -> tuple[str, tuple[PortalLocation, ...]]:
        """Submit the exact service selected by the user."""
        payload = self._wizard_payload(session)
        payload.update(
            {
                "_token": session.csrf_token,
                "id_specification": service.specification_id or "",
                "servicetype": LAPI_SERVICE_TYPE,
                "service": service.service_id,
                "service_name": service.description,
            }
        )
        response = await self._request(
            session,
            "POST",
            CREATE_PATH,
            operation="submit_service",
            data=payload,
        )
        return self._absorb_search_step(session, response.text)

    async def submit_doctor_choice(
        self, session: PortalSession, doctor: PortalDoctor, *, specification_id: str = ""
    ) -> tuple[str, tuple[PortalLocation, ...]]:
        """Submit the exact doctor selected by the user."""
        payload = self._wizard_payload(session)
        payload.update(
            {
                "_token": session.csrf_token,
                "id_specification": specification_id,
                "servicetype": LAPI_SERVICE_TYPE,
                "medico": doctor.raw_value,
            }
        )
        response = await self._request(
            session,
            "POST",
            CREATE_PATH,
            operation="submit_doctor",
            data=payload,
        )
        return self._absorb_search_step(session, response.text)

    def _wizard_payload(self, session: PortalSession) -> dict[str, str]:
        """Resubmit the wizard form as the browser does, choice excluded.

        The portal keeps part of its state in hidden fields and answers with a
        server error when they come back incomplete.
        """
        payload = parsers.parse_form_fields(session.wizard.html)
        payload.update(session.wizard.session_fields)
        return payload

    def _absorb_search_step(
        self, session: PortalSession, html: str
    ) -> tuple[str, tuple[PortalLocation, ...]]:
        locations = parsers.parse_location_options(html)
        session.wizard = WizardState(
            html=html,
            csrf_token=self._remember_csrf(session, html),
            patients=session.wizard.patients,
            locations=locations,
            doctor_services=parsers.parse_doctor_services(html),
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
        payload: dict[str, str | list[str]] = {"idPrestazione": str(service_id)}
        if location_ids:
            payload["idUnitaErogante[]"] = [str(value) for value in location_ids]
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
