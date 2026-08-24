"""A synthetic portal used by the service, worker and bot test suites.

The fake speaks the same HTTP contract as the real portal — the same routes,
the same form fields and the same JSON shapes — but it is fully deterministic
and can be steered to reproduce failures, lost slots and ambiguous outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from app.composition import Container, build_breaker_policy
from app.config import Settings
from app.db.session import Database
from app.portal.circuit_breaker import CircuitBreakerService
from app.portal.client import PortalClient
from app.portal.rate_limit import AsyncRateLimiter, NullPortalGate, RateLimitPolicy
from app.services.accounts import AccountService
from app.services.admin import AdminService
from app.services.appointments import AppointmentService
from app.services.bookings import BookingService
from app.services.confirmations import ConfirmationService
from app.services.notifications import NotificationService
from app.services.portal_sessions import PortalSessionManager
from app.services.search import SearchService
from app.services.users import InviteService, UserService
from app.services.watches import WatchService
from tests.fixtures.synthetic import load

PDF_BYTES = b"%PDF-1.7\n promemoria sintetico \n%%EOF"
VALID_EMAIL = "utente.sintetico@example.invalid"
VALID_PASSWORD = "password-sintetica-di-test"


def _html(body: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, text=body, headers={"content-type": "text/html; charset=utf-8"})


def _json(name: str) -> httpx.Response:
    return httpx.Response(
        200, content=load(name).encode("utf-8"), headers={"content-type": "application/json"}
    )


@dataclass(slots=True)
class FakePortal:
    """Configurable synthetic portal."""

    #: Credentials the fake accepts; anything else fails authentication.
    valid_credentials: tuple[str, str] = (VALID_EMAIL, VALID_PASSWORD)
    #: ``availability.json`` by default; set to an empty payload to simulate
    #: "no slot", or to a different fixture to change the offered proposals.
    availability_fixture: str = "availability.json"
    #: Appointment list returned before and after a booking.
    appointments_fixture: str = "appointments_list.html"
    appointments_after_booking: str = "appointments_list_after_booking.html"
    appointments_after_cancel: str = "appointments_list_after_cancel.html"
    #: When ``True`` the appointment list never changes after a confirmation,
    #: which is exactly what an ambiguous outcome looks like.
    booking_is_ambiguous: bool = False
    #: When ``True`` the cancellation leaves the appointment in place.
    cancellation_is_ambiguous: bool = False
    #: Forces a status code for every request; used for breaker tests.
    forced_status: int | None = None
    #: Number of failures to emit before answering normally.
    transient_failures: int = 0
    #: Set to ``True`` to require re-authentication on the next request.
    session_expired: bool = False
    pdf_content_type: str = "application/pdf"
    pdf_bytes: bytes = PDF_BYTES

    #: Set once a successful login has been performed in this fake.
    session_cookie: str = "synthetic-session"

    booked: bool = field(default=False, init=False)
    cancelled: bool = field(default=False, init=False)
    confirmations: int = field(default=0, init=False)
    cancel_calls: int = field(default=0, init=False)
    requests: list[str] = field(default_factory=list, init=False)

    def reset(self) -> None:
        """Restore the default behaviour between tests."""
        default = FakePortal()
        for name in (
            "valid_credentials",
            "availability_fixture",
            "appointments_fixture",
            "appointments_after_booking",
            "appointments_after_cancel",
            "booking_is_ambiguous",
            "cancellation_is_ambiguous",
            "forced_status",
            "transient_failures",
            "session_expired",
            "pdf_content_type",
            "pdf_bytes",
            "session_cookie",
        ):
            setattr(self, name, getattr(default, name))
        self.booked = False
        self.cancelled = False
        self.confirmations = 0
        self.cancel_calls = 0
        self.requests.clear()

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append(f"{request.method} {path}")

        if self.transient_failures > 0:
            self.transient_failures -= 1
            return httpx.Response(503, text="temporarily unavailable")
        if self.forced_status is not None:
            return httpx.Response(self.forced_status, text="forced")

        if path == "/login" and request.method == "GET":
            return _html(load("login_form.html"))
        if path == "/login":
            body = request.content.decode("utf-8")
            email, password = self.valid_credentials
            from urllib.parse import quote_plus

            if quote_plus(email) in body and quote_plus(password) in body:
                self.session_expired = False
                return httpx.Response(
                    302,
                    headers={
                        "location": "/appointments",
                        "set-cookie": f"opbg-prenotazione={self.session_cookie}",
                    },
                )
            return _html(load("login_failed.html"))

        # Authenticated routes require the session cookie, exactly like the
        # portal: a failed login therefore cannot be mistaken for a success.
        if self.session_expired or not self._has_session(request):
            return _html(load("login_form.html"))

        if path == "/user/info":
            return _json("user_info.json")
        if path == "/appointments":
            return _html(load(self._appointment_fixture()))
        if path == "/appointments/history":
            return _html(load("appointments_list_empty.html"))
        if path == "/appointments/create" and request.method == "GET":
            return _html(load("appointments_create_lapi.html"))
        if path == "/appointments/create":
            return self._handle_create(request)
        if path == "/service-autocomplete":
            return _json("service_autocomplete.json")
        if path == "/medici-autocomplete":
            return _json("doctor_autocomplete.json")
        if path == "/appointments/disponibilita":
            return _json(self.availability_fixture)
        if path.endswith("/pdf-download"):
            return httpx.Response(
                200, content=self.pdf_bytes, headers={"content-type": self.pdf_content_type}
            )
        if path.endswith("/cancel"):
            self.cancel_calls += 1
            if not self.cancellation_is_ambiguous:
                self.cancelled = True
            return httpx.Response(302, headers={"location": "/appointments"})
        return httpx.Response(404, text="not found")

    def _has_session(self, request: httpx.Request) -> bool:
        cookie = request.headers.get("cookie", "")
        return f"opbg-prenotazione={self.session_cookie}" in cookie

    def _appointment_fixture(self) -> str:
        if self.cancelled:
            return self.appointments_after_cancel
        if self.booked and not self.booking_is_ambiguous:
            return self.appointments_after_booking
        return self.appointments_fixture

    def _handle_create(self, request: httpx.Request) -> httpx.Response:
        body = request.content.decode("utf-8")
        if "tax_code=" in body:
            return _html(load("appointments_create_lapi.html"))
        if "confirm=true" in body:
            self.confirmations += 1
            self.booked = True
            return _html(load("appointments_confirm.html"))
        if "id_proposal=" in body:
            return _html(load("appointments_confirm.html"))
        if "service=" in body or "medico=" in body:
            return _html(load("appointments_create_locations.html"))
        return _html(load("appointments_create_slots.html"))


async def _no_sleep(_seconds: float) -> None:
    return None


def build_container(settings: Settings, database: Database, portal: FakePortal) -> Container:
    """Build a container whose portal adapter talks to the fake portal."""
    limiter = AsyncRateLimiter(
        RateLimitPolicy(
            max_concurrency=4,
            min_interval_seconds=0.0,
            per_account_min_interval_seconds=0.0,
        ),
        jitter=lambda: 0.0,
        sleep=_no_sleep,
    )
    client = PortalClient(
        settings,
        limiter=limiter,
        gate=NullPortalGate(),
        transport=httpx.MockTransport(portal.handle),
        sleep=_no_sleep,
        jitter=lambda: 0.0,
    )
    breaker = CircuitBreakerService(build_breaker_policy(settings))
    sessions = PortalSessionManager(settings, client, breaker)
    search = SearchService(settings, client, sessions)
    return Container(
        settings=settings,
        database=database,
        portal_client=client,
        breaker=breaker,
        sessions=sessions,
        users=UserService(settings),
        invites=InviteService(settings),
        accounts=AccountService(settings, client, sessions),
        search=search,
        bookings=BookingService(settings, client, search),
        appointments=AppointmentService(settings, client, sessions),
        watches=WatchService(settings),
        notifications=NotificationService(settings),
        confirmations=ConfirmationService(settings),
        admin=AdminService(settings, breaker),
    )


def make_settings(base: Settings, **overrides: object) -> Settings:
    """Build a settings instance with a few fields overridden."""
    values = base.model_dump()
    values.update(overrides)
    values["app_secret_key"] = base.app_secret_key.get_secret_value()
    values["app_encryption_key"] = base.app_encryption_key.get_secret_value()
    values["app_blind_index_key"] = base.app_blind_index_key.get_secret_value()
    values["telegram_bot_token"] = base.telegram_bot_token.get_secret_value()
    values["mysql_password"] = base.mysql_password.get_secret_value()
    return Settings(_env_file=None, **values)


class RecordingSender:
    """Collects the notifications the queue would deliver."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.failures = 0

    async def send(self, *, chat_id: int, text: str, keyboard: Any | None = None) -> int | None:
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("synthetic delivery failure")
        self.sent.append((chat_id, text))
        return len(self.sent)
