"""The synthetic portal keeps its fixtures in the future."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from tests.support.portal import FIXTURE_ANCHOR, fixture_shift, load


@pytest.mark.parametrize(
    "today",
    [date(2026, 3, 1), date(2026, 9, 15), date(2026, 10, 12), date(2031, 2, 28)],
)
def test_the_anchor_lands_one_to_two_weeks_ahead(today: date) -> None:
    moved = FIXTURE_ANCHOR + fixture_shift(today)
    assert moved.weekday() == FIXTURE_ANCHOR.weekday()
    if today <= FIXTURE_ANCHOR - timedelta(days=7):
        assert moved == FIXTURE_ANCHOR
    else:
        assert today + timedelta(days=7) <= moved < today + timedelta(days=14)


def test_both_date_formats_are_moved_consistently() -> None:
    shift = fixture_shift()
    availability = load("availability.json")
    appointments = load("appointments_list.html")
    assert (FIXTURE_ANCHOR + shift).isoformat() in availability
    assert (FIXTURE_ANCHOR + shift).strftime("%d/%m/%Y") in appointments


def test_daylight_saving_fixtures_keep_their_dates() -> None:
    assert "2026-03-29" in load("availability_dst.json")
