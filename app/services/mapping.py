"""Conversions between persisted rows and domain value objects."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from app.db.models.conversations import BookingDraft
from app.db.models.watches import BookingWatch, WatchLocation
from app.domain.entities import SearchSelection, SlotFilter, TimeWindow
from app.domain.enums import DateMode, LocationMode, SearchType, SortStrategy
from app.domain.filters import (
    parse_excluded_dates,
    parse_time_windows,
    serialize_excluded_dates,
    serialize_time_windows,
)


def draft_selection(draft: BookingDraft) -> SearchSelection:
    """Build the exact portal selection stored in a draft."""
    return SearchSelection(
        search_type=draft.search_type or SearchType.SERVICE,
        query_text=draft.query_text or "",
        service_id=draft.selected_service_external_id,
        service_name=draft.selected_service_name,
        specification_id=draft.selected_specification_id,
        doctor_id=draft.selected_doctor_external_id,
        doctor_name=draft.selected_doctor_name,
        preparation_note=draft.preparation_note,
        online_bookable=draft.service_online_bookable,
    )


def watch_selection(watch: BookingWatch) -> SearchSelection:
    return SearchSelection(
        search_type=watch.search_type,
        query_text=watch.query_text,
        service_id=watch.selected_service_external_id,
        service_name=watch.selected_service_name,
        specification_id=watch.selected_specification_id,
        doctor_id=watch.selected_doctor_external_id,
        doctor_name=watch.selected_doctor_name,
    )


def _location_ids(entries: Sequence[dict[str, str]]) -> tuple[str, ...]:
    return tuple(str(entry.get("id", "")) for entry in entries if entry.get("id"))


def draft_filter(draft: BookingDraft, *, max_results: int) -> SlotFilter:
    """Build the slot filter described by a booking draft."""
    return SlotFilter(
        date_mode=draft.date_mode,
        date_from=draft.date_from,
        date_to=draft.date_to,
        rolling_days=draft.rolling_days,
        min_lead_hours=draft.min_lead_hours,
        weekdays_mask=draft.weekdays_mask,
        time_windows=parse_time_windows(draft.time_windows_json),
        excluded_dates=parse_excluded_dates(draft.excluded_dates_json),
        location_mode=draft.location_mode,
        location_ids=_location_ids(draft.selected_locations_json),
        preferred_location_ids=_location_ids(draft.selected_locations_json),
        sort_strategy=draft.sort_strategy,
        max_results=max_results,
        required_doctor_id=draft.selected_doctor_external_id,
        required_service_id=draft.selected_service_external_id,
    )


def watch_filter(
    watch: BookingWatch, locations: Sequence[WatchLocation], *, max_results: int | None = None
) -> SlotFilter:
    """Build the slot filter described by a monitor and its locations."""
    ordered = sorted(locations, key=lambda item: (item.preference_order, item.location_name))
    ids = tuple(item.portal_location_id for item in ordered)
    return SlotFilter(
        date_mode=watch.date_mode,
        date_from=watch.date_from,
        date_to=watch.date_to,
        rolling_days=watch.rolling_days,
        min_lead_hours=watch.min_lead_hours,
        weekdays_mask=watch.weekdays_mask,
        time_windows=parse_time_windows(watch.time_windows_json),
        excluded_dates=parse_excluded_dates(watch.excluded_dates_json),
        location_mode=watch.location_mode,
        location_ids=ids,
        preferred_location_ids=ids,
        sort_strategy=watch.sort_strategy,
        max_results=max_results or watch.max_results,
        required_doctor_id=watch.selected_doctor_external_id,
        required_service_id=watch.selected_service_external_id,
    )


def apply_selection_to_draft(draft: BookingDraft, selection: SearchSelection) -> None:
    draft.search_type = selection.search_type
    draft.query_text = selection.query_text[:160]
    draft.selected_service_external_id = selection.service_id
    draft.selected_service_name = (
        None if selection.service_name is None else selection.service_name[:255]
    )
    draft.selected_specification_id = selection.specification_id
    draft.selected_doctor_external_id = (
        None if selection.doctor_id is None else selection.doctor_id[:128]
    )
    draft.selected_doctor_name = (
        None if selection.doctor_name is None else selection.doctor_name[:255]
    )
    draft.preparation_note = (
        None if selection.preparation_note is None else selection.preparation_note[:1024]
    )
    draft.service_online_bookable = selection.online_bookable


def copy_filters(source: BookingDraft, target: BookingWatch) -> None:
    """Copy the temporal and location filters from a draft onto a monitor."""
    target.location_mode = source.location_mode
    target.date_mode = source.date_mode
    target.date_from = source.date_from
    target.date_to = source.date_to
    target.rolling_days = source.rolling_days
    target.min_lead_hours = source.min_lead_hours
    target.weekdays_mask = source.weekdays_mask
    target.time_windows_json = list(source.time_windows_json)
    target.excluded_dates_json = list(source.excluded_dates_json)
    target.sort_strategy = source.sort_strategy


def set_time_windows(target: BookingDraft | BookingWatch, windows: Sequence[TimeWindow]) -> None:
    target.time_windows_json = serialize_time_windows(windows)


def set_excluded_dates(target: BookingDraft | BookingWatch, dates: Sequence[date]) -> None:
    target.excluded_dates_json = serialize_excluded_dates(dates)


def describe_locations(entries: Sequence[dict[str, str]]) -> str:
    if not entries:
        return "tutte le sedi"
    return ", ".join(str(entry.get("name", entry.get("id", ""))) for entry in entries)


DEFAULTS = {
    "date_mode": DateMode.ROLLING,
    "location_mode": LocationMode.ALL_DYNAMIC,
    "sort_strategy": SortStrategy.EARLIEST,
}
