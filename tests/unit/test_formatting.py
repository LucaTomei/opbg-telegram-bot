"""User facing formatting of portal values."""

from __future__ import annotations

from datetime import UTC, datetime

from app.bot import texts
from app.bot.presenters.formatting import (
    format_price,
    format_slot_button,
    selection_marker,
)
from app.domain.entities import PortalSlot


def slot(cost: str | None) -> PortalSlot:
    return PortalSlot(
        proposal_code="P1",
        location_id="11601",
        location_name="OPBG - Sede Sintetica",
        starts_at_local=datetime(2026, 8, 26, 17, 0),
        starts_at_utc=datetime(2026, 8, 26, 15, 0, tzinfo=UTC),
        cost=cost,
    )


def test_the_price_is_on_the_slot_button() -> None:
    """It is what people compare proposals on, so it must not need a scroll."""
    label = format_slot_button(slot("170"))
    assert label.startswith("26/08 17:00")
    assert "170 €" in label


def test_a_slot_without_a_price_reads_cleanly() -> None:
    label = format_slot_button(slot(None))
    assert "€" not in label
    assert label.startswith("26/08 17:00")


def test_a_price_that_already_carries_a_currency_is_left_alone() -> None:
    assert format_price("€ 170,00") == "€ 170,00"
    assert format_price("170 EUR") == "170 EUR"


def test_a_missing_price_renders_as_nothing() -> None:
    assert format_price(None) == ""
    assert format_price("   ") == ""


def test_the_two_selection_states_cannot_be_mistaken_for_each_other() -> None:
    """An on row and an off row must not both read as ticked.

    ``☑️`` used to mark a chosen row; on iOS it draws as a pale grey box that,
    next to a green tick, reads as the off state.
    """
    assert selection_marker(True) != selection_marker(False)
    assert selection_marker(True) == "✅"
    assert "✅" not in selection_marker(False)


def test_a_switchable_label_carries_no_marker_of_its_own() -> None:
    """The keyboard adds the marker.

    A label holding its own tick showed one while switched off, and two while
    switched on.
    """
    assert not texts.BOOK_ALL_LOCATIONS.lstrip().startswith(("✅", "☑️", "⬜️"))
