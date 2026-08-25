"""Opt-in, non-destructive checks against the real portal.

These tests are skipped unless ``OPBG_LIVE_TESTS=1`` and real credentials are
provided in the environment.  They never create or cancel an appointment, and
they are disabled in continuous integration by default.

    OPBG_LIVE_TESTS=1 \
    OPBG_LIVE_EMAIL=... OPBG_LIVE_PASSWORD=... \
    .venv/bin/python -m pytest tests/contract/test_live_portal.py -m live

Booking and cancellation against the real portal are deliberately not
automated: see ``docs/IMPLEMENTATION_STATUS.md`` for the manual runbook.
"""

from __future__ import annotations

import os

import pytest

from app.config import Settings
from app.portal.client import PortalClient
from app.portal.models import PortalCredentials
from app.portal.rate_limit import AsyncRateLimiter, RateLimitPolicy

# The suite-wide timeout is sized for offline tests. These talk to a real
# portal and deliberately pace themselves, so they need a wider budget.
pytestmark = [pytest.mark.live, pytest.mark.timeout(300)]

LIVE_ENABLED = os.environ.get("OPBG_LIVE_TESTS") == "1"
LIVE_EMAIL = os.environ.get("OPBG_LIVE_EMAIL", "")
LIVE_PASSWORD = os.environ.get("OPBG_LIVE_PASSWORD", "")
LIVE_QUERY = os.environ.get("OPBG_LIVE_QUERY", "visita")
#: Empty means "whatever the application ships as its default". The host is
#: deliberately not written here: a guard test forbids naming the real portal
#: anywhere under tests/.
LIVE_BASE_URL = os.environ.get("OPBG_LIVE_BASE_URL", "")

requires_live = pytest.mark.skipif(
    not (LIVE_ENABLED and LIVE_EMAIL and LIVE_PASSWORD),
    reason="live portal tests are opt-in; set OPBG_LIVE_TESTS=1 and the credentials",
)


@pytest.fixture
def live_settings(settings: Settings) -> Settings:
    """Point at the portal the application ships as its default.

    The shared fixture is pinned to a synthetic host so that no test can reach
    the real one by accident. These tests are the exception, and they take the
    address from the application rather than repeating it here.
    """
    base_url = LIVE_BASE_URL or str(Settings.model_fields["portal_base_url"].default)
    return settings.model_copy(update={"portal_base_url": base_url})


def live_client(settings: Settings) -> PortalClient:
    limiter = AsyncRateLimiter(
        RateLimitPolicy(
            max_concurrency=1,
            min_interval_seconds=2.0,
            per_account_min_interval_seconds=2.0,
        )
    )
    return PortalClient(settings, limiter=limiter)


@requires_live
async def test_login_and_session_verification(live_settings: Settings) -> None:
    client = live_client(live_settings)
    async with client.session(account_key="live") as session:
        result = await client.login(session, PortalCredentials(LIVE_EMAIL, LIVE_PASSWORD))
        assert result.verified is True
        assert await client.verify_session(session) is True


@requires_live
async def test_patients_and_autocomplete_are_readable(live_settings: Settings) -> None:
    client = live_client(live_settings)
    async with client.session(account_key="live") as session:
        await client.login(session, PortalCredentials(LIVE_EMAIL, LIVE_PASSWORD))
        patients = await client.open_lapi_wizard(session)
        assert patients, "the account exposes no patient"
        tax_code = patients[0].tax_code
        services = await client.autocomplete_services(session, query=LIVE_QUERY, tax_code=tax_code)
        assert isinstance(services, tuple)


@requires_live
async def test_the_service_choice_exposes_the_locations(live_settings: Settings) -> None:
    """At least one bookable service must yield the locations that serve it.

    Not every service is offered online, and one that is not exposes no
    location at all. What must never happen is that *every* service comes back
    without one: that is the signature of a parser that no longer matches the
    portal, and it surfaces to the user as a generic failure.
    """
    client = live_client(live_settings)
    found: dict[str, list[str]] = {}
    async with client.session(account_key="live") as session:
        await client.login(session, PortalCredentials(LIVE_EMAIL, LIVE_PASSWORD))
        patients = await client.open_lapi_wizard(session)
        await client.select_patient(session, patients[0].tax_code)
        services = await client.autocomplete_services(
            session, query=LIVE_QUERY, tax_code=patients[0].tax_code
        )
        candidates = [item for item in services if item.online_bookable][:3]
        if not candidates:
            pytest.skip("the live query returned no service bookable online")

        for candidate in candidates:
            # Each attempt restarts the wizard: a submitted step is spent.
            await client.open_lapi_wizard(session)
            await client.select_patient(session, patients[0].tax_code)
            _, locations = await client.submit_service_choice(session, candidate)
            found[candidate.service_id] = [item.location_id for item in locations]
            if locations:
                break

    assert any(found.values()), f"no service exposed a location: {found}"


@requires_live
async def test_availability_is_readable(live_settings: Settings) -> None:
    """Availability must be readable for a service the portal offers online.

    A service with no location is not a failure: the portal simply does not
    take that booking online, and the application reports it as such.
    """
    client = live_client(live_settings)
    async with client.session(account_key="live") as session:
        await client.login(session, PortalCredentials(LIVE_EMAIL, LIVE_PASSWORD))
        patients = await client.open_lapi_wizard(session)
        services = await client.autocomplete_services(
            session, query=LIVE_QUERY, tax_code=patients[0].tax_code
        )
        candidates = [item for item in services if item.online_bookable][:3]
        if not candidates:
            pytest.skip("the live query returned no service bookable online")

        for candidate in candidates:
            await client.open_lapi_wizard(session)
            await client.select_patient(session, patients[0].tax_code)
            _, locations = await client.submit_service_choice(session, candidate)
            if locations:
                break
        else:
            pytest.skip("no service in this query is taken online")

        availability = await client.fetch_availability(
            session,
            service_id=candidate.service_id,
            location_ids=[item.location_id for item in locations],
        )

    assert availability is not None
    assert availability.locations or availability.slots is not None


@requires_live
async def test_appointment_list_is_readable(live_settings: Settings) -> None:
    client = live_client(live_settings)
    async with client.session(account_key="live") as session:
        await client.login(session, PortalCredentials(LIVE_EMAIL, LIVE_PASSWORD))
        appointments = await client.list_appointments(session)
        assert isinstance(appointments, tuple)


@requires_live
async def test_pdf_of_an_explicitly_selected_appointment(live_settings: Settings) -> None:
    """Downloads the reminder of the appointment named in the environment."""
    appointment_id = os.environ.get("OPBG_LIVE_APPOINTMENT_ID", "")
    if not appointment_id:
        pytest.skip("set OPBG_LIVE_APPOINTMENT_ID to exercise the PDF download")
    client = live_client(live_settings)
    async with client.session(account_key="live") as session:
        await client.login(session, PortalCredentials(LIVE_EMAIL, LIVE_PASSWORD))
        document = await client.download_pdf(
            session, appointment_id=appointment_id, max_bytes=live_settings.pdf_max_bytes
        )
        assert document.content.startswith(b"%PDF-")
