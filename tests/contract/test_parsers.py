"""Portal parser contract tests against synthetic fixtures."""

from __future__ import annotations

import orjson
import pytest

from app.domain.errors import PortalContractChanged
from app.domain.filters import ROME
from app.portal import parsers
from app.portal.forms import extract_csrf_token, extract_form, parse_document
from tests.fixtures.synthetic import load


def json_fixture(name: str) -> object:
    return orjson.loads(load(name))


# --- CSRF and forms ----------------------------------------------------------
def test_csrf_is_read_from_the_hidden_input_first() -> None:
    document = parse_document(load("login_form.html"))
    assert extract_csrf_token(document) == "SYNTHETIC-FORM-CSRF-0001"


def test_csrf_falls_back_to_the_meta_tag() -> None:
    html = '<html><head><meta name="csrf-token" content="ONLY-META"></head><body></body></html>'
    assert extract_csrf_token(parse_document(html)) == "ONLY-META"


def test_missing_csrf_raises_a_contract_error() -> None:
    with pytest.raises(PortalContractChanged):
        extract_csrf_token(parse_document("<html><body>niente</body></html>"))


def test_form_defaults_come_from_the_document() -> None:
    form = parsers.parse_login_form(load("login_form.html"))
    assert form.action == "/login"
    assert form.fields["_token"] == "SYNTHETIC-FORM-CSRF-0001"
    assert form.fields["to_service"] == "lapi"
    assert form.fields["email"] == ""
    assert form.fields["password"] == ""


def test_login_form_requires_credentials_fields() -> None:
    html = '<form action="/login"><input name="_token" value="x"></form>'
    with pytest.raises(PortalContractChanged):
        parsers.parse_login_form(html)


def test_checkbox_defaults_reflect_the_checked_attribute() -> None:
    form = extract_form(
        parse_document(load("appointments_create_lapi.html")), selector_id="appointment-form"
    )
    assert form.fields["privacy"] == "1"
    assert form.fields["newsletter"] == ""


# --- authentication ----------------------------------------------------------
def test_login_page_is_not_authenticated() -> None:
    assert parsers.looks_authenticated(load("login_form.html")) is False


def test_appointment_page_is_authenticated() -> None:
    assert parsers.looks_authenticated(load("appointments_list.html")) is True


def test_user_info_drops_tokens() -> None:
    metadata = parsers.parse_user_info(json_fixture("user_info.json"))
    assert metadata == {"id": 4242, "name": "Utente", "surname": "Sintetico"}
    assert "access_token" not in metadata
    assert "refresh_token" not in metadata


def test_user_info_rejects_a_non_object_payload() -> None:
    with pytest.raises(PortalContractChanged):
        parsers.parse_user_info(["not", "an", "object"])


# --- patients ----------------------------------------------------------------
def test_patients_are_parsed_from_the_select() -> None:
    patients = parsers.parse_patients(load("appointments_create_lapi.html"))
    assert [patient.tax_code for patient in patients] == [
        "AAABBB01A01A001A",
        "CCCDDD02B02B002B",
    ]
    assert patients[0].display_name == "Alfa Beta"
    assert patients[0].relation == "figlio"


def test_patients_without_the_select_raise_a_contract_error() -> None:
    with pytest.raises(PortalContractChanged):
        parsers.parse_patients(load("contract_changed.html"))


# --- autocomplete ------------------------------------------------------------
def test_services_are_normalised() -> None:
    services = parsers.parse_services(json_fixture("service_autocomplete.json"))
    assert len(services) == 3
    first = services[0]
    assert first.service_id == "9001"
    assert first.description == "VISITA SINTETICA"
    assert first.online_bookable is True
    assert first.specification_id is None
    second = services[1]
    assert second.specification_id == "44"
    assert second.preparation_note == "Presentarsi a digiuno da 8 ore."
    assert services[2].online_bookable is False


def test_services_tolerate_missing_optional_keys() -> None:
    services = parsers.parse_services(json_fixture("service_autocomplete_partial.json"))
    assert [service.service_id for service in services] == ["9101"]


def test_empty_service_payload_returns_no_result() -> None:
    assert parsers.parse_services(json_fixture("service_autocomplete_empty.json")) == ()


def test_service_payload_of_the_wrong_shape_raises() -> None:
    with pytest.raises(PortalContractChanged):
        parsers.parse_services("not a list")


def test_doctors_are_normalised() -> None:
    """The submitted value is ``cognome_nome``, as the portal builds it.

    The description is what the user reads; sending it as the value earns a
    server error on the step that follows.
    """
    doctors = parsers.parse_doctors(json_fixture("doctor_autocomplete.json"))
    assert [doctor.raw_value for doctor in doctors] == [
        "SINTETICO_UNO",
        "SINTETICA_DUE",
        "SENZA_DESCRIZIONE",
    ]
    assert [doctor.description for doctor in doctors] == [
        "DOTT. SINTETICO UNO",
        "DOTT.SSA SINTETICA DUE",
        "SENZA DESCRIZIONE",
    ]
    assert doctors[0].gender == "MAS"


def test_empty_doctor_payload_returns_no_result() -> None:
    assert parsers.parse_doctors(json_fixture("doctor_autocomplete_empty.json")) == ()


def test_doctor_payload_of_the_wrong_shape_raises() -> None:
    with pytest.raises(PortalContractChanged):
        parsers.parse_doctors(42)


# --- availability ------------------------------------------------------------
def test_availability_is_normalised_with_locations_and_doctors() -> None:
    availability = parsers.parse_availability(json_fixture("availability.json"))
    assert {location.location_id for location in availability.locations} == {"101", "202"}
    assert len(availability.slots) == 3
    first = next(slot for slot in availability.slots if slot.proposal_code == "PROP-0001")
    assert first.location_id == "101"
    assert first.location_name == "Sede Alfa"
    assert first.doctor_id == "M-1"
    assert first.doctor_name == "DOTT. SINTETICO UNO"
    assert first.starts_at_local.tzinfo is not None
    assert first.starts_at_local.hour == 9
    assert first.starts_at_utc.hour == 7  # CEST is UTC+2 in September


def test_availability_counts_slots_per_location() -> None:
    availability = parsers.parse_availability(json_fixture("availability.json"))
    counts = {location.location_id: location.slot_count for location in availability.locations}
    assert counts == {"101": 1, "202": 2}


def test_empty_availability_returns_no_slot() -> None:
    availability = parsers.parse_availability(json_fixture("availability_empty.json"))
    assert availability.slots == ()
    assert availability.locations == ()


def test_availability_tolerates_missing_keys() -> None:
    availability = parsers.parse_availability(json_fixture("availability_missing_keys.json"))
    assert len(availability.slots) == 1
    assert availability.slots[0].proposal_code == "PROP-9999"
    assert availability.slots[0].location_name == "Sede Gamma"


def test_broken_availability_raises_a_contract_error() -> None:
    with pytest.raises(PortalContractChanged):
        parsers.parse_availability(json_fixture("availability_broken.json"))


def test_availability_handles_daylight_saving_transitions() -> None:
    availability = parsers.parse_availability(json_fixture("availability_dst.json"))
    by_code = {slot.proposal_code: slot for slot in availability.slots}
    # 29 March 2026: Italy moves from CET (+1) to CEST (+2) at 02:00 local.
    assert by_code["PROP-DST-1"].starts_at_utc.hour == 0  # 01:30 CET -> 00:30 UTC
    assert by_code["PROP-DST-1"].starts_at_utc.minute == 30
    assert by_code["PROP-DST-2"].starts_at_utc.hour == 1  # 03:30 CEST -> 01:30 UTC
    # 25 October 2026: the clock falls back; zoneinfo resolves the first instant.
    assert by_code["PROP-DST-3"].starts_at_local.tzinfo is ROME


def test_server_rendered_availability_is_parsed() -> None:
    availability = parsers.parse_server_rendered_availability(load("availability_rendered.html"))
    assert {slot.proposal_code for slot in availability.slots} == {"PROP-R001", "PROP-R002"}
    assert {location.name for location in availability.locations} == {"Sede Alfa", "Sede Beta"}


def test_server_rendered_availability_falls_back_to_hidden_fields() -> None:
    availability = parsers.parse_server_rendered_availability(
        load("appointments_create_slots.html")
    )
    assert {slot.proposal_code for slot in availability.slots} == {"PROP-0001", "PROP-0002"}


def test_location_options_are_read_from_checkboxes() -> None:
    locations = parsers.parse_location_options(load("appointments_create_locations.html"))
    assert {(item.location_id, item.name) for item in locations} == {
        ("101", "Sede Alfa"),
        ("202", "Sede Beta"),
    }


def test_the_appointment_day_is_read_from_the_card_header() -> None:
    """The card writes the day in its header and only the hour among the fields.

    An appointment left undated cannot be sorted, reminded about or compared
    against the portal on the next sync, so the header is the fallback.
    """
    appointments = parsers.parse_appointments(load("appointments_list_header_date.html"))
    assert len(appointments) == 1
    appointment = appointments[0]
    assert appointment.external_id == "90001"
    assert appointment.scheduled_at_local is not None
    assert appointment.scheduled_at_local.date().isoformat() == "2026-09-12"
    assert appointment.scheduled_at_local.strftime("%H:%M") == "10:40"
    # Europe/Rome is two hours ahead of UTC in September.
    assert appointment.scheduled_at_utc is not None
    assert appointment.scheduled_at_utc.strftime("%H:%M") == "08:40"
    assert appointment.pdf_path == "/appointments/90001/pdf-download"
    assert appointment.cancel_path == "/appointments/90001/cancel"


def test_the_payment_label_is_not_mistaken_for_the_appointment_status() -> None:
    """The portal writes "Stato" over the payment, and the card carries the real one.

    Reading the labelled field leaves every appointment "non determinato" while
    the card plainly says it is pending.
    """
    appointments = parsers.parse_appointments(load("appointments_list_header_date.html"))
    appointment = appointments[0]
    assert appointment.status_hint == "pending"
    assert appointment.payment_note is not None
    assert "pagato" in appointment.payment_note.casefold()


def test_an_empty_availability_serialised_as_an_array_is_not_a_contract_change() -> None:
    """PHP serialises an empty associative array as ``[]``.

    The portal answers that way when nothing is available, so treating it as a
    broken contract turned every fruitless search into a technical error.
    """
    availability = parsers.parse_availability(json_fixture("availability_php_empty.json"))
    assert availability.slots == ()
    assert availability.locations == ()


def test_a_non_empty_availability_field_of_the_wrong_type_is_still_refused() -> None:
    with pytest.raises(PortalContractChanged):
        parsers.parse_availability({"dates_locations": ["unexpected"]})


def test_the_services_a_doctor_performs_are_read_from_the_step() -> None:
    """Choosing a doctor is not enough: the portal wants a service as well.

    The list lives in a hidden field holding JSON whose quotes the portal does
    not escape, so it cannot be read through the attribute.
    """
    services = parsers.parse_doctor_services(load("appointments_create_doctor_units.html"))
    assert [(item.service_id, item.description) for item in services] == [
        ("9001", "VISITA SINTETICA"),
        ("9002", "VISITA SINTETICA AVANZATA"),
    ]


def test_a_step_without_doctor_units_yields_no_service() -> None:
    assert parsers.parse_doctor_services(load("appointments_create_locations.html")) == ()


def test_location_options_are_read_from_the_radio_group() -> None:
    """The portal renders one radio per location, valued ``location-<id>``.

    The identifier carries a prefix and the group opens with a ``location-all``
    entry that stands for every location rather than for one.
    """
    locations = parsers.parse_location_options(load("appointments_create_location_radios.html"))
    assert {(item.location_id, item.name) for item in locations} == {
        ("3001", "Sede Gamma"),
        ("3002", "Sede Delta"),
    }


def test_the_every_location_radio_is_not_a_location() -> None:
    locations = parsers.parse_location_options(load("appointments_create_location_radios.html"))
    assert "all" not in {item.location_id for item in locations}
    assert "location-all" not in {item.location_id for item in locations}


def test_session_fields_are_preserved_between_steps() -> None:
    fields = parsers.parse_session_service_fields(load("appointments_create_locations.html"))
    assert fields["session_id_prestazione"] == "9001"
    assert fields["session_name_prestazione"] == "VISITA SINTETICA"


# --- appointments ------------------------------------------------------------
def test_appointments_are_parsed_by_label_not_by_position() -> None:
    appointments = parsers.parse_appointments(load("appointments_list.html"))
    assert len(appointments) == 2
    first, second = appointments
    assert first.external_id == "70001"
    assert first.service_name == "VISITA SINTETICA"
    assert first.doctor_name == "DOTT. SINTETICO UNO"
    assert first.location_name == "Sede Alfa"
    assert first.scheduled_at_local is not None
    assert first.scheduled_at_local.hour == 9
    assert first.cancel_path == "/appointments/70001/cancel"
    assert first.pdf_path == "/appointments/70001/pdf-download"
    # The second card lists the same labels in a different order.
    assert second.service_name == "CONTROLLO SINTETICO"
    assert second.scheduled_at_local is not None
    assert second.scheduled_at_local.hour == 15


def test_appointment_fingerprint_is_deterministic() -> None:
    first = parsers.parse_appointments(load("appointments_list.html"))
    second = parsers.parse_appointments(load("appointments_list.html"))
    assert [item.raw_fingerprint for item in first] == [item.raw_fingerprint for item in second]


def test_empty_appointment_list_is_supported() -> None:
    assert parsers.parse_appointments(load("appointments_list_empty.html")) == ()


def test_appointment_container_change_raises() -> None:
    with pytest.raises(PortalContractChanged):
        parsers.parse_appointments(load("contract_changed.html"))
    with pytest.raises(PortalContractChanged):
        parsers.parse_appointments("")


def test_confirmation_form_is_found() -> None:
    form = parsers.parse_confirmation_form(load("appointments_confirm.html"))
    assert form.fields["_token"] == "SYNTHETIC-FORM-CSRF-0005"
    assert form.has_submit is True


def test_slot_selection_form_is_matched_by_proposal() -> None:
    form = parsers.parse_slot_selection_form(load("appointments_create_slots.html"), "PROP-0002")
    assert form.fields["location_name"] == "Sede Beta"
    assert form.fields["selected_date"] == "2026-09-16 15:00"


def test_unknown_proposal_raises_a_contract_error() -> None:
    with pytest.raises(PortalContractChanged):
        parsers.parse_slot_selection_form(load("appointments_create_slots.html"), "PROP-MISSING")


# --- challenges and errors ---------------------------------------------------
def test_captcha_is_detected() -> None:
    assert parsers.detect_portal_challenge(load("captcha_challenge.html")) == "g-recaptcha"


def test_normal_page_has_no_challenge() -> None:
    assert parsers.detect_portal_challenge(load("appointments_list.html")) is None


def test_error_messages_are_collected_and_truncated() -> None:
    messages = parsers.parse_error_messages(load("login_failed.html"))
    assert messages == ("Credenziali non valide.",)


# --- normalisation helpers ---------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-09-15", (2026, 9, 15)),
        ("15/09/2026", (2026, 9, 15)),
        ("15-09-2026", (2026, 9, 15)),
        ("15 settembre 2026", (2026, 9, 15)),
    ],
)
def test_dates_are_parsed_in_every_observed_format(
    raw: str, expected: tuple[int, int, int]
) -> None:
    parsed = parsers.parse_date(raw)
    assert parsed is not None
    assert (parsed.year, parsed.month, parsed.day) == expected


@pytest.mark.parametrize(("raw", "expected"), [("09:30", (9, 30)), ("ore 14.05", (14, 5))])
def test_times_are_parsed(raw: str, expected: tuple[int, int]) -> None:
    parsed = parsers.parse_time(raw)
    assert parsed is not None
    assert (parsed.hour, parsed.minute) == expected


def test_label_normalisation_ignores_accents_and_punctuation() -> None:
    assert parsers.normalize_label("  Prestazione:  ") == "prestazione"
    assert parsers.normalize_label("Sedè") == "sede"
