"""Deterministic slot filtering, ordering and scheduling unit tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.domain.entities import PortalSlot, SlotFilter, TimeWindow
from app.domain.enums import ALL_WEEKDAYS_MASK, DateMode, LocationMode, SortStrategy
from app.domain.filters import (
    ROME,
    apply_filter,
    combine_local,
    deduplicate,
    in_quiet_hours,
    local_naive_to_utc_naive,
    next_check_at,
    parse_excluded_dates,
    parse_time_windows,
    resolve_period,
    serialize_excluded_dates,
    serialize_time_windows,
    to_local,
    to_utc,
    toggle_weekday,
    weekday_selected,
)

NOW = datetime(2026, 9, 1, 6, 0, tzinfo=UTC)


def slot(
    day: date,
    moment: time,
    *,
    code: str = "P",
    location_id: str = "101",
    location_name: str = "Sede Alfa",
    doctor_id: str | None = None,
    service_id: str | None = "9001",
) -> PortalSlot:
    local = combine_local(day, moment, ROME)
    return PortalSlot(
        proposal_code=code,
        starts_at_local=local,
        starts_at_utc=to_utc(local),
        location_id=location_id,
        location_name=location_name,
        doctor_id=doctor_id,
        service_id=service_id,
    )


def base_filter(**overrides: object) -> SlotFilter:
    defaults: dict[str, object] = {
        "date_mode": DateMode.ROLLING,
        "rolling_days": 60,
        "weekdays_mask": ALL_WEEKDAYS_MASK,
        "max_results": 100,
        "required_service_id": None,
    }
    defaults.update(overrides)
    return SlotFilter(**defaults)  # type: ignore[arg-type]


# --- period ------------------------------------------------------------------
def test_rolling_window_is_inclusive_on_both_ends() -> None:
    slots = [
        slot(date(2026, 9, 1), time(9, 0), code="today"),
        slot(date(2026, 10, 31), time(9, 0), code="last"),
        slot(date(2026, 11, 1), time(9, 0), code="after"),
    ]
    kept = apply_filter(slots, base_filter(rolling_days=60), now=NOW)
    assert {item.proposal_code for item in kept} == {"today", "last"}


def test_fixed_period_bounds_are_inclusive() -> None:
    slots = [
        slot(date(2026, 9, 9), time(9, 0), code="before"),
        slot(date(2026, 9, 10), time(9, 0), code="start"),
        slot(date(2026, 9, 20), time(9, 0), code="end"),
        slot(date(2026, 9, 21), time(9, 0), code="after"),
    ]
    kept = apply_filter(
        slots,
        base_filter(
            date_mode=DateMode.FIXED,
            date_from=date(2026, 9, 10),
            date_to=date(2026, 9, 20),
            rolling_days=None,
        ),
        now=NOW,
    )
    assert {item.proposal_code for item in kept} == {"start", "end"}


def test_resolve_period_returns_the_expected_bounds() -> None:
    lower, upper = resolve_period(base_filter(rolling_days=30), today=date(2026, 9, 1))
    assert lower == date(2026, 9, 1)
    assert upper == date(2026, 10, 1)
    lower, upper = resolve_period(
        base_filter(
            date_mode=DateMode.FIXED, date_from=date(2026, 1, 1), date_to=date(2026, 2, 1)
        ),
        today=date(2026, 9, 1),
    )
    assert (lower, upper) == (date(2026, 1, 1), date(2026, 2, 1))


def test_rolling_window_without_a_limit_has_no_upper_bound() -> None:
    lower, upper = resolve_period(base_filter(rolling_days=None), today=date(2026, 9, 1))
    assert lower == date(2026, 9, 1)
    assert upper is None


# --- lead time ---------------------------------------------------------------
def test_minimum_lead_time_excludes_imminent_slots() -> None:
    slots = [
        slot(date(2026, 9, 1), time(9, 0), code="soon"),  # 07:00 UTC, one hour away
        slot(date(2026, 9, 1), time(20, 0), code="later"),
    ]
    kept = apply_filter(slots, base_filter(min_lead_hours=6), now=NOW)
    assert {item.proposal_code for item in kept} == {"later"}


def test_past_slots_are_always_excluded() -> None:
    slots = [slot(date(2026, 8, 31), time(9, 0), code="past")]
    assert apply_filter(slots, base_filter(), now=NOW) == []


# --- excluded dates ----------------------------------------------------------
def test_excluded_dates_are_removed() -> None:
    slots = [
        slot(date(2026, 9, 10), time(9, 0), code="keep"),
        slot(date(2026, 9, 11), time(9, 0), code="drop"),
    ]
    kept = apply_filter(
        slots, base_filter(excluded_dates=frozenset({date(2026, 9, 11)})), now=NOW
    )
    assert {item.proposal_code for item in kept} == {"keep"}


# --- weekdays ----------------------------------------------------------------
def test_weekday_mask_selects_only_the_requested_days() -> None:
    monday = date(2026, 9, 7)
    saturday = date(2026, 9, 12)
    slots = [
        slot(monday, time(9, 0), code="mon"),
        slot(saturday, time(9, 0), code="sat"),
    ]
    weekdays_only = 0b0011111
    kept = apply_filter(slots, base_filter(weekdays_mask=weekdays_only), now=NOW)
    assert {item.proposal_code for item in kept} == {"mon"}


def test_weekday_helpers_are_consistent() -> None:
    mask = 0
    for index in range(7):
        assert weekday_selected(mask, index) is False
        mask = toggle_weekday(mask, index)
        assert weekday_selected(mask, index) is True
    assert mask == ALL_WEEKDAYS_MASK
    assert weekday_selected(toggle_weekday(mask, 0), 0) is False


# --- time windows ------------------------------------------------------------
def test_multiple_time_windows_are_supported() -> None:
    slots = [
        slot(date(2026, 9, 10), time(7, 59), code="early"),
        slot(date(2026, 9, 10), time(8, 0), code="start-1"),
        slot(date(2026, 9, 10), time(12, 30), code="end-1"),
        slot(date(2026, 9, 10), time(13, 0), code="gap"),
        slot(date(2026, 9, 10), time(16, 0), code="start-2"),
        slot(date(2026, 9, 10), time(19, 31), code="late"),
    ]
    windows = (
        TimeWindow(start=time(8, 0), end=time(12, 30)),
        TimeWindow(start=time(16, 0), end=time(19, 30)),
    )
    kept = apply_filter(slots, base_filter(time_windows=windows), now=NOW)
    assert {item.proposal_code for item in kept} == {"start-1", "end-1", "start-2"}


def test_a_window_crossing_midnight_is_supported() -> None:
    window = TimeWindow(start=time(22, 0), end=time(2, 0))
    assert window.contains(time(23, 0)) is True
    assert window.contains(time(1, 0)) is True
    assert window.contains(time(12, 0)) is False


def test_no_window_means_any_time() -> None:
    slots = [slot(date(2026, 9, 10), time(3, 0), code="night")]
    assert len(apply_filter(slots, base_filter(), now=NOW)) == 1


def test_time_windows_serialise_and_parse_back() -> None:
    windows = (TimeWindow(start=time(8, 0), end=time(12, 30)),)
    payload = serialize_time_windows(windows)
    assert payload == [{"start": "08:00", "end": "12:30"}]
    assert parse_time_windows(payload) == windows


def test_malformed_time_windows_are_ignored() -> None:
    assert parse_time_windows([{"start": "08:00"}, {}]) == ()


def test_excluded_dates_serialise_and_parse_back() -> None:
    values = [date(2026, 9, 11), date(2026, 9, 10)]
    payload = serialize_excluded_dates(values)
    assert payload == ["2026-09-10", "2026-09-11"]
    assert parse_excluded_dates([*payload, "not-a-date"]) == frozenset(values)


# --- locations ---------------------------------------------------------------
def test_selected_locations_filter_slots() -> None:
    slots = [
        slot(date(2026, 9, 10), time(9, 0), code="alfa", location_id="101"),
        slot(date(2026, 9, 10), time(9, 0), code="beta", location_id="202"),
    ]
    kept = apply_filter(
        slots,
        base_filter(location_mode=LocationMode.SELECTED, location_ids=("202",)),
        now=NOW,
    )
    assert {item.proposal_code for item in kept} == {"beta"}


def test_all_dynamic_keeps_new_locations() -> None:
    slots = [
        slot(date(2026, 9, 10), time(9, 0), code="alfa", location_id="101"),
        slot(date(2026, 9, 10), time(9, 0), code="nuova", location_id="999"),
    ]
    kept = apply_filter(slots, base_filter(location_mode=LocationMode.ALL_DYNAMIC), now=NOW)
    assert len(kept) == 2


def test_preferred_location_ordering_is_applied() -> None:
    slots = [
        slot(date(2026, 9, 10), time(9, 0), code="alfa-early", location_id="101"),
        slot(date(2026, 9, 9), time(9, 0), code="beta-earlier", location_id="202"),
    ]
    kept = apply_filter(
        slots,
        base_filter(
            sort_strategy=SortStrategy.PREFERRED_LOCATION_FIRST,
            preferred_location_ids=("101", "202"),
        ),
        now=NOW,
    )
    assert [item.proposal_code for item in kept] == ["alfa-early", "beta-earlier"]


def test_earliest_ordering_ignores_location_preference() -> None:
    slots = [
        slot(date(2026, 9, 10), time(9, 0), code="alfa", location_id="101"),
        slot(date(2026, 9, 9), time(9, 0), code="beta", location_id="202"),
    ]
    kept = apply_filter(
        slots,
        base_filter(
            sort_strategy=SortStrategy.EARLIEST, preferred_location_ids=("101", "202")
        ),
        now=NOW,
    )
    assert [item.proposal_code for item in kept] == ["beta", "alfa"]


# --- identity ----------------------------------------------------------------
def test_a_doctor_search_never_degrades_to_another_doctor() -> None:
    slots = [
        slot(date(2026, 9, 10), time(9, 0), code="wanted", doctor_id="M-1"),
        slot(date(2026, 9, 10), time(10, 0), code="other", doctor_id="M-2"),
    ]
    kept = apply_filter(slots, base_filter(required_doctor_id="M-1"), now=NOW)
    assert {item.proposal_code for item in kept} == {"wanted"}


def test_slots_without_doctor_information_are_kept_for_a_doctor_search() -> None:
    slots = [slot(date(2026, 9, 10), time(9, 0), code="unknown", doctor_id=None)]
    kept = apply_filter(slots, base_filter(required_doctor_id="M-1"), now=NOW)
    assert len(kept) == 1


def test_a_service_search_keeps_only_the_selected_service() -> None:
    slots = [
        slot(date(2026, 9, 10), time(9, 0), code="wanted", service_id="9001"),
        slot(date(2026, 9, 10), time(10, 0), code="other", service_id="9002"),
    ]
    kept = apply_filter(slots, base_filter(required_service_id="9001"), now=NOW)
    assert {item.proposal_code for item in kept} == {"wanted"}


# --- limits and deduplication ------------------------------------------------
def test_result_limit_is_applied_after_ordering() -> None:
    slots = [
        slot(date(2026, 9, 10 + index), time(9, 0), code=f"p{index}") for index in range(10)
    ]
    kept = apply_filter(slots, base_filter(max_results=3), now=NOW)
    assert [item.proposal_code for item in kept] == ["p0", "p1", "p2"]


def test_duplicate_proposals_are_removed() -> None:
    first = slot(date(2026, 9, 10), time(9, 0), code="dup")
    second = slot(date(2026, 9, 10), time(9, 0), code="dup")
    assert len(deduplicate([first, second])) == 1


# --- timezone ----------------------------------------------------------------
def test_local_conversion_uses_europe_rome() -> None:
    winter = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    summer = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    assert to_local(winter).hour == 13
    assert to_local(summer).hour == 14


def test_naive_local_times_convert_to_naive_utc() -> None:
    assert local_naive_to_utc_naive(datetime(2026, 7, 15, 14, 0)) == datetime(2026, 7, 15, 12, 0)
    assert local_naive_to_utc_naive(datetime(2026, 1, 15, 13, 0)) == datetime(2026, 1, 15, 12, 0)


def test_combine_local_attaches_the_italian_timezone() -> None:
    combined = combine_local(date(2026, 7, 15), time(9, 0))
    assert combined.tzinfo is ROME
    assert combined.utcoffset() == timedelta(hours=2)


def test_slots_are_compared_in_utc_across_a_dst_change() -> None:
    before = slot(date(2026, 3, 28), time(23, 0), code="before")
    after = slot(date(2026, 3, 29), time(3, 0), code="after")
    assert before.starts_at_utc < after.starts_at_utc


def test_custom_timezone_is_supported() -> None:
    moment = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    assert to_local(moment, ZoneInfo("UTC")).hour == 12


# --- scheduling --------------------------------------------------------------
def test_next_check_uses_the_interval_and_jitter() -> None:
    now = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    result = next_check_at(
        now=now,
        interval_seconds=300,
        jitter_seconds=30,
        consecutive_errors=0,
        backoff_base_seconds=1.5,
        backoff_max_seconds=3600,
        jitter_value=0.5,
    )
    assert result == now + timedelta(seconds=315)


def test_backoff_grows_with_consecutive_errors() -> None:
    now = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    delays = [
        (
            next_check_at(
                now=now,
                interval_seconds=120,
                jitter_seconds=0,
                consecutive_errors=errors,
                backoff_base_seconds=60,
                backoff_max_seconds=3600,
                jitter_value=0.0,
            )
            - now
        ).total_seconds()
        for errors in (0, 1, 2, 3, 10)
    ]
    assert delays == [120.0, 120.0, 120.0, 240.0, 3600.0]


def test_backoff_never_exceeds_the_maximum() -> None:
    now = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    result = next_check_at(
        now=now,
        interval_seconds=120,
        jitter_seconds=0,
        consecutive_errors=30,
        backoff_base_seconds=60,
        backoff_max_seconds=900,
        jitter_value=0.0,
    )
    assert (result - now).total_seconds() == 900


# --- quiet hours -------------------------------------------------------------
@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (time(23, 0), True),
        (time(6, 59), True),
        (time(7, 0), False),
        (time(12, 0), False),
        (time(21, 59), False),
        (time(22, 0), True),
    ],
)
def test_quiet_hours_across_midnight(moment: time, expected: bool) -> None:
    assert in_quiet_hours(moment, time(22, 0), time(7, 0)) is expected


@pytest.mark.parametrize(
    ("moment", "expected"),
    [(time(13, 0), True), (time(15, 0), False), (time(14, 0), False)],
)
def test_quiet_hours_within_a_day(moment: time, expected: bool) -> None:
    assert in_quiet_hours(moment, time(13, 0), time(14, 0)) is expected


def test_quiet_hours_disabled_when_not_configured() -> None:
    assert in_quiet_hours(time(23, 0), None, None) is False
    assert in_quiet_hours(time(23, 0), time(22, 0), time(22, 0)) is False
