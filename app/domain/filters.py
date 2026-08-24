"""Deterministic slot filtering, ordering and time-window handling.

The filter order is fixed by ``docs/06-monitoring-and-autobook.md``:

1. fixed period or rolling window;
2. minimum lead time;
3. excluded dates;
4. weekdays;
5. time windows;
6. included locations;
7. exact doctor/service;
8. ordering by preferred location, date and time;
9. result limit.

All comparisons happen in ``Europe/Rome`` local time, which is also what the
portal displays, while storage and equality use UTC.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.domain.entities import PortalSlot, SlotFilter, TimeWindow
from app.domain.enums import DateMode, LocationMode, SortStrategy

ROME = ZoneInfo("Europe/Rome")

MAX_ROLLING_DAYS = 365
MAX_TIME_WINDOWS = 6
MAX_EXCLUDED_DATES = 60


def to_local(moment: datetime, timezone: ZoneInfo = ROME) -> datetime:
    """Convert an instant to local time, assuming UTC when naive."""
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return aware.astimezone(timezone)


def to_utc(moment: datetime, timezone: ZoneInfo = ROME) -> datetime:
    """Convert a local datetime to UTC, assuming ``timezone`` when naive."""
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone)
    return aware.astimezone(UTC)


def local_naive_to_utc_naive(moment: datetime, timezone: ZoneInfo = ROME) -> datetime:
    """Convert a naive local datetime into the naive UTC value stored in MySQL."""
    return to_utc(moment, timezone).replace(tzinfo=None)


def combine_local(day: date, moment: time, timezone: ZoneInfo = ROME) -> datetime:
    """Combine a local date and time into an aware local datetime.

    During the spring-forward DST transition the requested wall-clock time may
    not exist; Python folds it forward, which is the behaviour the portal shows.
    """
    return datetime.combine(day, moment).replace(tzinfo=timezone)


def resolve_period(slot_filter: SlotFilter, *, today: date) -> tuple[date | None, date | None]:
    """Return the inclusive local date bounds implied by the filter."""
    if slot_filter.date_mode is DateMode.FIXED:
        return slot_filter.date_from, slot_filter.date_to
    days = slot_filter.rolling_days
    if days is None:
        return today, None
    return today, today + timedelta(days=max(days, 0))


def parse_time_windows(raw: Iterable[dict[str, str]]) -> tuple[TimeWindow, ...]:
    """Parse persisted time windows, ignoring malformed entries deterministically."""
    windows: list[TimeWindow] = []
    for entry in raw:
        start = entry.get("start")
        end = entry.get("end")
        if not start or not end:
            continue
        windows.append(TimeWindow(start=time.fromisoformat(start), end=time.fromisoformat(end)))
    return tuple(windows[:MAX_TIME_WINDOWS])


def serialize_time_windows(windows: Sequence[TimeWindow]) -> list[dict[str, str]]:
    return [window.to_json() for window in windows]


def parse_excluded_dates(raw: Iterable[str]) -> frozenset[date]:
    parsed: set[date] = set()
    for entry in raw:
        try:
            parsed.add(date.fromisoformat(entry))
        except ValueError:
            continue
    return frozenset(sorted(parsed)[:MAX_EXCLUDED_DATES])


def serialize_excluded_dates(dates: Iterable[date]) -> list[str]:
    return [value.isoformat() for value in sorted(dates)]


def weekday_selected(mask: int, weekday_index: int) -> bool:
    """``weekday_index`` follows :meth:`datetime.date.weekday` (Monday == 0)."""
    return bool(mask & (1 << weekday_index))


def toggle_weekday(mask: int, weekday_index: int) -> int:
    return mask ^ (1 << weekday_index)


def _passes_period(slot: PortalSlot, lower: date | None, upper: date | None) -> bool:
    day = slot.slot_date
    if lower is not None and day < lower:
        return False
    return not (upper is not None and day > upper)


def _passes_lead_time(slot: PortalSlot, earliest_utc: datetime) -> bool:
    return slot.starts_at_utc >= earliest_utc


def _passes_time_windows(slot: PortalSlot, windows: Sequence[TimeWindow]) -> bool:
    if not windows:
        return True
    moment = slot.slot_time
    return any(window.contains(moment) for window in windows)


def _passes_locations(slot: PortalSlot, slot_filter: SlotFilter) -> bool:
    if slot_filter.location_mode is LocationMode.ALL_DYNAMIC:
        return True
    return slot.location_id in slot_filter.location_ids


def _passes_identity(slot: PortalSlot, slot_filter: SlotFilter) -> bool:
    """A doctor search must never silently degrade to any doctor."""
    if slot_filter.required_doctor_id and slot.doctor_id:
        return slot.doctor_id == slot_filter.required_doctor_id
    if slot_filter.required_service_id and slot.service_id:
        return str(slot.service_id) == str(slot_filter.required_service_id)
    return True


def sort_key(slot_filter: SlotFilter) -> object:
    preference = {
        location_id: index for index, location_id in enumerate(slot_filter.preferred_location_ids)
    }
    fallback = len(preference)

    def key(slot: PortalSlot) -> tuple[int, datetime, str]:
        if slot_filter.sort_strategy is SortStrategy.PREFERRED_LOCATION_FIRST:
            return (
                preference.get(slot.location_id, fallback),
                slot.starts_at_utc,
                slot.location_name,
            )
        return (0, slot.starts_at_utc, slot.location_name)

    return key


def apply_filter(
    slots: Iterable[PortalSlot],
    slot_filter: SlotFilter,
    *,
    now: datetime,
    timezone: ZoneInfo = ROME,
) -> list[PortalSlot]:
    """Apply every filter in the documented order and return the ordered slots."""
    now_utc = now.astimezone(UTC) if now.tzinfo else now.replace(tzinfo=UTC)
    today_local = now_utc.astimezone(timezone).date()
    lower, upper = resolve_period(slot_filter, today=today_local)
    earliest_utc = now_utc + timedelta(hours=max(slot_filter.min_lead_hours, 0))

    selected: list[PortalSlot] = []
    for slot in slots:
        if not _passes_period(slot, lower, upper):
            continue
        if not _passes_lead_time(slot, earliest_utc):
            continue
        if slot.slot_date in slot_filter.excluded_dates:
            continue
        if not weekday_selected(slot_filter.weekdays_mask, slot.slot_date.weekday()):
            continue
        if not _passes_time_windows(slot, slot_filter.time_windows):
            continue
        if not _passes_locations(slot, slot_filter):
            continue
        if not _passes_identity(slot, slot_filter):
            continue
        selected.append(slot)

    selected.sort(key=sort_key(slot_filter))  # type: ignore[arg-type]
    limit = max(slot_filter.max_results, 1)
    return selected[:limit]


def deduplicate(slots: Iterable[PortalSlot]) -> list[PortalSlot]:
    """Remove duplicate proposals keeping the first occurrence."""
    seen: set[tuple[str, str, datetime]] = set()
    unique: list[PortalSlot] = []
    for slot in slots:
        key = (slot.proposal_code, slot.location_id, slot.starts_at_utc)
        if key in seen:
            continue
        seen.add(key)
        unique.append(slot)
    return unique


def next_check_at(
    *,
    now: datetime,
    interval_seconds: int,
    jitter_seconds: int,
    consecutive_errors: int,
    backoff_base_seconds: float,
    backoff_max_seconds: int,
    jitter_value: float,
) -> datetime:
    """Compute the next execution instant with jitter and exponential backoff.

    ``jitter_value`` is a caller supplied value in ``[0, 1)`` so scheduling stays
    deterministic and testable.
    """
    delay = float(interval_seconds)
    if consecutive_errors > 0:
        backoff = backoff_base_seconds * (2 ** min(consecutive_errors - 1, 16))
        delay = min(max(delay, backoff), float(backoff_max_seconds))
    jitter = jitter_value * max(jitter_seconds, 0)
    return (now + timedelta(seconds=delay + jitter)).replace(microsecond=0)


def in_quiet_hours(
    moment_local: time,
    start: time | None,
    end: time | None,
) -> bool:
    """Return ``True`` when a local time falls inside the quiet-hours window."""
    if start is None or end is None or start == end:
        return False
    if start < end:
        return start <= moment_local < end
    return moment_local >= start or moment_local < end
