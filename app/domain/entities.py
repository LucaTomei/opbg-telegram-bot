"""Immutable value objects exchanged between the portal adapter and services.

These types intentionally do not depend on SQLAlchemy, aiogram or httpx: they
are the shared vocabulary of the application layer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any

from app.domain.enums import DateMode, LocationMode, SearchType, SortStrategy

#: A cookie jar as it travels between the portal adapter and storage.
#:
#: Each record carries the scope of the cookie, not only its value: the portal
#: sets ``XSRF-TOKEN`` on more than one path, and a flat name/value mapping
#: cannot hold two cookies with the same name. Reading one back by name raises
#: ``httpx.CookieConflict``.
#:
#: The mapping form is the previous, lossy format, still accepted when loading
#: a jar stored before this became a list.
type CookieJar = Sequence[Mapping[str, str]] | Mapping[str, str]


@dataclass(frozen=True, slots=True)
class PortalPatient:
    """A patient option parsed from the LAPI booking form."""

    tax_code: str
    display_name: str
    relation: str | None = None

    @property
    def external_id(self) -> str:
        return self.tax_code


@dataclass(frozen=True, slots=True)
class PortalService:
    """A bookable service returned by ``/service-autocomplete``."""

    service_id: str
    description: str
    online_bookable: bool
    specification_id: str | None = None
    group_id: str | None = None
    preparation_note: str | None = None
    cost: str | None = None
    counter_booking: bool = False
    phone_booking: bool = False

    @property
    def label(self) -> str:
        return self.description


@dataclass(frozen=True, slots=True)
class PortalDoctor:
    """A doctor returned by ``/medici-autocomplete``."""

    raw_value: str
    description: str
    last_name: str
    first_name: str
    gender: str | None = None

    @property
    def external_id(self) -> str:
        return self.raw_value

    @property
    def label(self) -> str:
        return self.description or f"{self.last_name} {self.first_name}".strip()


@dataclass(frozen=True, slots=True)
class PortalLocation:
    """A location (``unità erogante``) offered for the current search."""

    location_id: str
    name: str
    slot_count: int = 0


@dataclass(frozen=True, slots=True)
class PortalSlot:
    """A normalised availability proposal.

    ``starts_at_local`` is timezone aware in ``Europe/Rome``; ``starts_at_utc``
    is the UTC instant used for storage and comparisons.
    """

    proposal_code: str
    starts_at_local: datetime
    starts_at_utc: datetime
    location_id: str
    location_name: str
    clinic_name: str | None = None
    agenda_id: str | None = None
    agenda_name: str | None = None
    service_id: str | None = None
    doctor_id: str | None = None
    doctor_name: str | None = None
    cost: str | None = None
    raw_date_value: str | None = None
    form_fields: dict[str, str] = field(default_factory=dict)

    @property
    def slot_date(self) -> date:
        return self.starts_at_local.date()

    @property
    def slot_time(self) -> time:
        return self.starts_at_local.time()


@dataclass(frozen=True, slots=True)
class PortalAvailability:
    """Full availability payload for one search."""

    locations: tuple[PortalLocation, ...]
    slots: tuple[PortalSlot, ...]
    doctors: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PortalAppointment:
    """An appointment parsed from the portal appointment list."""

    external_id: str
    service_name: str | None
    doctor_name: str | None
    patient_label: str | None
    location_name: str | None
    scheduled_at_local: datetime | None
    scheduled_at_utc: datetime | None
    status_hint: str | None
    payment_note: str | None
    cancel_path: str | None
    pdf_path: str | None
    raw_fingerprint: str


@dataclass(frozen=True, slots=True)
class TimeWindow:
    """An inclusive local time window such as 08:00–12:30."""

    start: time
    end: time

    def contains(self, moment: time) -> bool:
        if self.start <= self.end:
            return self.start <= moment <= self.end
        # A window crossing midnight, e.g. 22:00–02:00.
        return moment >= self.start or moment <= self.end

    def to_json(self) -> dict[str, str]:
        return {"start": self.start.strftime("%H:%M"), "end": self.end.strftime("%H:%M")}

    @classmethod
    def from_json(cls, payload: dict[str, str]) -> TimeWindow:
        return cls(
            start=time.fromisoformat(payload["start"]),
            end=time.fromisoformat(payload["end"]),
        )


@dataclass(frozen=True, slots=True)
class SlotFilter:
    """The complete, validated set of user filters applied to raw slots."""

    date_mode: DateMode = DateMode.ROLLING
    date_from: date | None = None
    date_to: date | None = None
    rolling_days: int | None = 30
    min_lead_hours: int = 0
    weekdays_mask: int = 0b1111111
    time_windows: tuple[TimeWindow, ...] = ()
    excluded_dates: frozenset[date] = frozenset()
    location_mode: LocationMode = LocationMode.ALL_DYNAMIC
    location_ids: tuple[str, ...] = ()
    preferred_location_ids: tuple[str, ...] = ()
    sort_strategy: SortStrategy = SortStrategy.EARLIEST
    max_results: int = 40
    required_doctor_id: str | None = None
    required_service_id: str | None = None

    def describe_weekdays(self) -> tuple[int, ...]:
        return tuple(index for index in range(7) if self.weekdays_mask & (1 << index))


@dataclass(frozen=True, slots=True)
class SearchSelection:
    """The exact portal result a draft or watch is bound to."""

    search_type: SearchType
    query_text: str
    service_id: str | None = None
    service_name: str | None = None
    specification_id: str | None = None
    doctor_id: str | None = None
    doctor_name: str | None = None
    preparation_note: str | None = None
    online_bookable: bool = True


@dataclass(frozen=True, slots=True)
class BookingOutcome:
    """Result of a reconciled booking attempt."""

    succeeded: bool
    ambiguous: bool
    appointment_external_id: str | None
    detail: str
    appointment: PortalAppointment | None = None


@dataclass(frozen=True, slots=True)
class PdfDocument:
    """A validated appointment reminder PDF held only in memory."""

    filename: str
    content: bytes

    @property
    def size(self) -> int:
        return len(self.content)


@dataclass(frozen=True, slots=True)
class LoginResult:
    """Outcome of a portal login attempt."""

    verified: bool
    cookies: CookieJar
    account_metadata: dict[str, Any] = field(default_factory=dict)
