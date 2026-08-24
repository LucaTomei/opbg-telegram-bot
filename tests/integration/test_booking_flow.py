"""Manual booking, reconciliation, cancellation and PDF delivery."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.composition import Container
from app.db.models.users import TelegramUser
from app.db.repositories.base import utc_now
from app.db.repositories.bookings import AppointmentRepository, BookingAttemptRepository
from app.domain.entities import SearchSelection, SlotFilter
from app.domain.enums import (
    AppointmentStatus,
    BookingAttemptStatus,
    DateMode,
    SearchType,
)
from app.domain.errors import PdfUnavailable
from app.services.bookings import BookingRequest
from tests.support.factories import make_account, make_patient
from tests.support.portal import FakePortal

SELECTION = SearchSelection(
    search_type=SearchType.SERVICE,
    query_text="visita",
    service_id="9001",
    service_name="VISITA SINTETICA",
)
FILTER = SlotFilter(
    date_mode=DateMode.ROLLING,
    rolling_days=3650,
    max_results=20,
    required_service_id="9001",
)


async def prepare(session: AsyncSession, user: TelegramUser):
    account = await make_account(session, user)
    patient = await make_patient(session, account)
    return account, patient


async def collect_slots(
    session: AsyncSession, container: Container, account, patient
):
    result = await container.search.collect(
        session, account, patient, SELECTION, FILTER, now=utc_now()
    )
    return result.slots


async def test_search_returns_the_exact_service_results(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    account, patient = await prepare(clean_session, user)
    services = await container.search.search_services(
        clean_session, account, patient, query="visita"
    )
    assert [service.service_id for service in services] == ["9001", "9002", "9003"]


async def test_search_returns_doctor_results(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    account, patient = await prepare(clean_session, user)
    doctors = await container.search.search_doctors(
        clean_session, account, patient, query="sintetico"
    )
    assert doctors[0].raw_value == "DOTT. SINTETICO UNO"


async def test_availability_is_collected_and_filtered(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    account, patient = await prepare(clean_session, user)
    result = await container.search.collect(
        clean_session, account, patient, SELECTION, FILTER, now=utc_now()
    )
    assert len(result.slots) == 3
    assert {location.location_id for location in result.locations} == {"101", "202"}


async def test_location_filter_restricts_the_result(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    from app.domain.enums import LocationMode

    account, patient = await prepare(clean_session, user)
    restricted = SlotFilter(
        date_mode=DateMode.ROLLING,
        rolling_days=3650,
        location_mode=LocationMode.SELECTED,
        location_ids=("202",),
        max_results=20,
    )
    result = await container.search.collect(
        clean_session, account, patient, SELECTION, restricted, now=utc_now()
    )
    assert {slot.location_id for slot in result.slots} == {"202"}


async def test_booking_is_confirmed_and_reconciled(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    portal: FakePortal,
) -> None:
    account, patient = await prepare(clean_session, user)
    slots = await collect_slots(clean_session, container, account, patient)
    target = next(slot for slot in slots if slot.proposal_code == "PROP-0001")
    # The synthetic portal publishes the new appointment on 20 October.
    portal.appointments_after_booking = "appointments_list_after_booking.html"
    request = BookingRequest(
        account=account,
        patient=patient,
        selection=SELECTION,
        slot_filter=FILTER,
        slot=target,
        user_id=user.id,
    )
    result = await container.bookings.book(clean_session, request)
    assert portal.confirmations == 1
    assert result.outcome.succeeded is False or result.outcome.ambiguous is False


async def test_booking_reconciles_the_new_appointment(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    portal: FakePortal,
) -> None:
    account, patient = await prepare(clean_session, user)
    portal.availability_fixture = "availability.json"
    slots = await collect_slots(clean_session, container, account, patient)
    target = slots[0]
    # Make the appointment list return a card matching the booked slot.
    portal.appointments_after_booking = "appointments_list.html"
    portal.appointments_fixture = "appointments_list_empty.html"
    result = await container.bookings.book(
        clean_session,
        BookingRequest(
            account=account,
            patient=patient,
            selection=SELECTION,
            slot_filter=FILTER,
            slot=target,
            user_id=user.id,
        ),
    )
    assert result.outcome.succeeded is True
    assert result.attempt.status is BookingAttemptStatus.SUCCEEDED
    stored = await AppointmentRepository(clean_session).find_by_external_id(
        account.id, result.outcome.appointment_external_id or ""
    )
    assert stored is not None
    assert stored.status is AppointmentStatus.PENDING


async def test_ambiguous_outcome_is_recorded_without_retry(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    portal: FakePortal,
) -> None:
    account, patient = await prepare(clean_session, user)
    slots = await collect_slots(clean_session, container, account, patient)
    portal.booking_is_ambiguous = True
    result = await container.bookings.book(
        clean_session,
        BookingRequest(
            account=account,
            patient=patient,
            selection=SELECTION,
            slot_filter=FILTER,
            slot=slots[0],
            user_id=user.id,
        ),
    )
    assert result.outcome.ambiguous is True
    assert result.attempt.status is BookingAttemptStatus.AMBIGUOUS
    assert portal.confirmations == 1


async def test_a_repeated_request_never_confirms_twice(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    portal: FakePortal,
) -> None:
    account, patient = await prepare(clean_session, user)
    slots = await collect_slots(clean_session, container, account, patient)
    portal.appointments_fixture = "appointments_list_empty.html"
    portal.appointments_after_booking = "appointments_list.html"
    request = BookingRequest(
        account=account,
        patient=patient,
        selection=SELECTION,
        slot_filter=FILTER,
        slot=slots[0],
        user_id=user.id,
    )
    first = await container.bookings.book(clean_session, request)
    second = await container.bookings.book(clean_session, request)
    assert first.duplicate is False
    assert second.duplicate is True
    assert portal.confirmations == 1


async def test_a_lost_slot_is_reported_without_confirmation(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    portal: FakePortal,
) -> None:
    account, patient = await prepare(clean_session, user)
    slots = await collect_slots(clean_session, container, account, patient)
    portal.availability_fixture = "availability_empty.json"
    result = await container.bookings.book(
        clean_session,
        BookingRequest(
            account=account,
            patient=patient,
            selection=SELECTION,
            slot_filter=FILTER,
            slot=slots[0],
            user_id=user.id,
        ),
    )
    assert result.attempt.status is BookingAttemptStatus.SLOT_LOST
    assert portal.confirmations == 0


async def test_a_slot_outside_the_filters_is_never_booked(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    portal: FakePortal,
) -> None:
    """The slot is re-validated against the filters inside the account lock."""
    account, patient = await prepare(clean_session, user)
    slots = await collect_slots(clean_session, container, account, patient)
    target = next(slot for slot in slots if slot.location_id == "202")
    from app.domain.enums import LocationMode

    narrow = SlotFilter(
        date_mode=DateMode.ROLLING,
        rolling_days=3650,
        location_mode=LocationMode.SELECTED,
        location_ids=("101",),
        max_results=20,
    )
    result = await container.bookings.book(
        clean_session,
        BookingRequest(
            account=account,
            patient=patient,
            selection=SELECTION,
            slot_filter=narrow,
            slot=target,
            user_id=user.id,
        ),
    )
    assert result.attempt.status is BookingAttemptStatus.SLOT_LOST
    assert portal.confirmations == 0


# --- appointments ------------------------------------------------------------
async def test_appointments_are_synchronised(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    account, _ = await prepare(clean_session, user)
    result = await container.appointments.sync_account(clean_session, account)
    assert result.created == 2
    page = await container.appointments.list_for_user(
        clean_session, user, page=0, page_size=10
    )
    assert page.total == 2


async def test_disappeared_appointments_are_marked_cancelled(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    portal: FakePortal,
) -> None:
    account, _ = await prepare(clean_session, user)
    await container.appointments.sync_account(clean_session, account)
    portal.appointments_fixture = "appointments_list_after_cancel.html"
    result = await container.appointments.sync_account(clean_session, account)
    assert result.cancelled == 1


async def test_cancellation_is_verified_on_the_portal(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    portal: FakePortal,
) -> None:
    account, patient = await prepare(clean_session, user)
    await container.appointments.sync_account(clean_session, account)
    appointments = AppointmentRepository(clean_session)
    target = await appointments.find_by_external_id(account.id, "70001")
    assert target is not None
    target.patient_id = patient.id
    await appointments.flush()

    outcome = await container.appointments.cancel(
        clean_session, user=user, appointment=target
    )
    assert outcome.succeeded is True
    assert target.status is AppointmentStatus.CANCELLED
    assert portal.cancel_calls == 1


async def test_ambiguous_cancellation_is_not_retried(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    portal: FakePortal,
) -> None:
    account, patient = await prepare(clean_session, user)
    await container.appointments.sync_account(clean_session, account)
    appointments = AppointmentRepository(clean_session)
    target = await appointments.find_by_external_id(account.id, "70001")
    assert target is not None
    target.patient_id = patient.id
    await appointments.flush()
    portal.cancellation_is_ambiguous = True

    outcome = await container.appointments.cancel(
        clean_session, user=user, appointment=target
    )
    assert outcome.ambiguous is True
    assert target.status is AppointmentStatus.PENDING
    assert portal.cancel_calls == 1


async def test_a_repeated_cancellation_callback_is_idempotent(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    portal: FakePortal,
) -> None:
    account, patient = await prepare(clean_session, user)
    await container.appointments.sync_account(clean_session, account)
    appointments = AppointmentRepository(clean_session)
    target = await appointments.find_by_external_id(account.id, "70001")
    assert target is not None
    target.patient_id = patient.id
    await appointments.flush()

    first = await container.appointments.cancel(clean_session, user=user, appointment=target)
    second = await container.appointments.cancel(clean_session, user=user, appointment=target)
    assert first.succeeded is True
    assert second.duplicate is True
    assert portal.cancel_calls == 1


# --- PDF ---------------------------------------------------------------------
async def test_pdf_is_delivered_and_not_persisted(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    account, _ = await prepare(clean_session, user)
    await container.appointments.sync_account(clean_session, account)
    target = await AppointmentRepository(clean_session).find_by_external_id(account.id, "70001")
    assert target is not None
    document = await container.appointments.download_pdf(clean_session, appointment=target)
    assert document.content.startswith(b"%PDF-")
    assert document.filename.endswith(".pdf")


async def test_invalid_pdf_is_refused(
    clean_session: AsyncSession,
    container: Container,
    user: TelegramUser,
    portal: FakePortal,
) -> None:
    account, _ = await prepare(clean_session, user)
    await container.appointments.sync_account(clean_session, account)
    target = await AppointmentRepository(clean_session).find_by_external_id(account.id, "70001")
    assert target is not None
    portal.pdf_content_type = "text/html"
    with pytest.raises(PdfUnavailable):
        await container.appointments.download_pdf(clean_session, appointment=target)


async def test_one_time_download_link_is_issued(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    account, _ = await prepare(clean_session, user)
    await container.appointments.sync_account(clean_session, account)
    target = await AppointmentRepository(clean_session).find_by_external_id(account.id, "70001")
    assert target is not None
    url = await container.appointments.issue_download_link(
        clean_session, user=user, appointment=target
    )
    assert url.startswith(container.settings.public_base_url + "/download/")


async def test_attempts_are_visible_to_the_owner(
    clean_session: AsyncSession, container: Container, user: TelegramUser
) -> None:
    account, patient = await prepare(clean_session, user)
    slots = await collect_slots(clean_session, container, account, patient)
    await container.bookings.book(
        clean_session,
        BookingRequest(
            account=account,
            patient=patient,
            selection=SELECTION,
            slot_filter=FILTER,
            slot=slots[0],
            user_id=user.id,
        ),
    )
    page = await BookingAttemptRepository(clean_session).list_for_user(
        user.id, page=0, page_size=10
    )
    assert page.total == 1
