"""Search and availability orchestration for both manual booking and monitors.

The portal keeps its wizard state server side, so every availability probe
replays the same sequence inside one session: open the LAPI wizard, select the
patient, submit the exact service or doctor, resolve the locations and read the
availability.  The stored search is re-resolved through autocomplete on every
run so a change of internal portal identifiers is detected instead of silently
booking the wrong thing.
"""

from __future__ import annotations

import unicodedata
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models.accounts import Patient, PortalAccount
from app.domain.entities import (
    PortalAvailability,
    PortalDoctor,
    PortalLocation,
    PortalService,
    PortalSlot,
    SearchSelection,
    SlotFilter,
)
from app.domain.enums import LocationMode, SearchType
from app.domain.errors import (
    AmbiguousSearchResult,
    DoctorServiceRequired,
    NoAvailability,
    NotFoundError,
    ServiceNotBookableOnline,
    ValidationError,
)
from app.domain.filters import apply_filter, deduplicate
from app.logging import get_logger
from app.portal.client import PortalClient, PortalSession
from app.services.portal_sessions import PortalSessionManager

logger = get_logger(__name__)

MIN_QUERY_LENGTH = 3
MAX_QUERY_LENGTH = 120


def normalize_for_match(value: str) -> str:
    """Normalise a description so stored and fresh results can be compared."""
    text = unicodedata.normalize("NFKD", value).casefold()
    stripped = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(stripped.split())


def validate_query(query: str) -> str:
    cleaned = " ".join(query.strip().split())
    if len(cleaned) < MIN_QUERY_LENGTH:
        raise ValidationError("the search text must contain at least 3 characters")
    return cleaned[:MAX_QUERY_LENGTH]


@dataclass(slots=True)
class AvailabilityFlow:
    """A portal session that has reached the availability stage."""

    portal_session: PortalSession
    selection: SearchSelection
    tax_code: str
    locations: tuple[PortalLocation, ...]
    availability: PortalAvailability | None = None


@dataclass(frozen=True, slots=True)
class AvailabilityResult:
    """Filtered availability plus the locations the portal offered."""

    locations: tuple[PortalLocation, ...]
    slots: tuple[PortalSlot, ...]
    total_before_filter: int


class SearchService:
    """Autocomplete and availability retrieval."""

    def __init__(
        self,
        settings: Settings,
        client: PortalClient,
        sessions: PortalSessionManager,
    ) -> None:
        self._settings = settings
        self._client = client
        self._sessions = sessions

    async def search_services(
        self,
        session: AsyncSession,
        account: PortalAccount,
        patient: Patient,
        *,
        query: str,
    ) -> tuple[PortalService, ...]:
        cleaned = validate_query(query)
        tax_code = self._require_tax_code(patient)
        async with self._sessions.authenticated_session(session, account) as portal_session:
            await self._client.open_lapi_wizard(portal_session)
            return await self._client.autocomplete_services(
                portal_session, query=cleaned, tax_code=tax_code
            )

    async def search_doctors(
        self,
        session: AsyncSession,
        account: PortalAccount,
        patient: Patient,
        *,
        query: str,
    ) -> tuple[PortalDoctor, ...]:
        cleaned = validate_query(query)
        tax_code = self._require_tax_code(patient)
        async with self._sessions.authenticated_session(session, account) as portal_session:
            await self._client.open_lapi_wizard(portal_session)
            return await self._client.autocomplete_doctors(
                portal_session, query=cleaned, tax_code=tax_code
            )

    @staticmethod
    def _require_tax_code(patient: Patient) -> str:
        tax_code = patient.external_tax_code
        if not tax_code:
            raise NotFoundError("the patient identifier is missing")
        return tax_code

    @asynccontextmanager
    async def availability_flow(
        self,
        session: AsyncSession,
        account: PortalAccount,
        patient: Patient,
        selection: SearchSelection,
    ) -> AsyncIterator[AvailabilityFlow]:
        """Drive the wizard up to the availability stage and keep it open."""
        tax_code = self._require_tax_code(patient)
        async with self._sessions.authenticated_session(session, account) as portal_session:
            await self._client.open_lapi_wizard(portal_session)
            await self._client.select_patient(portal_session, tax_code)
            resolved = await self._resolve_selection(portal_session, selection, tax_code)
            if resolved.search_type is SearchType.SERVICE:
                service = PortalService(
                    service_id=resolved.service_id or "",
                    description=resolved.service_name or "",
                    online_bookable=resolved.online_bookable,
                    specification_id=resolved.specification_id,
                    preparation_note=resolved.preparation_note,
                )
                _, locations = await self._client.submit_service_choice(portal_session, service)
            else:
                doctor = PortalDoctor(
                    raw_value=resolved.doctor_id or "",
                    description=resolved.doctor_name or "",
                    last_name="",
                    first_name="",
                )
                _, locations = await self._client.submit_doctor_choice(
                    portal_session,
                    doctor,
                    specification_id=resolved.specification_id or "",
                )
                resolved = self._with_doctor_service(resolved, portal_session)
            yield AvailabilityFlow(
                portal_session=portal_session,
                selection=resolved,
                tax_code=tax_code,
                locations=locations,
            )

    def _with_doctor_service(
        self, selection: SearchSelection, portal_session: PortalSession
    ) -> SearchSelection:
        """Pin the service the availability request needs for a doctor search.

        A doctor alone is not enough: the portal wants a service as well. When
        the doctor performs exactly one there is nothing to ask, otherwise only
        the person searching can choose.
        """
        if selection.service_id:
            return selection
        offered = portal_session.wizard.doctor_services
        if not offered:
            return selection
        if len(offered) == 1:
            only = offered[0]
            return replace(selection, service_id=only.service_id, service_name=only.description)
        raise DoctorServiceRequired(
            "the doctor performs more than one service",
            services=tuple((item.service_id, item.description) for item in offered),
        )

    async def _resolve_selection(
        self, portal_session: PortalSession, selection: SearchSelection, tax_code: str
    ) -> SearchSelection:
        """Re-run autocomplete and reconcile the stored choice by description.

        Portal identifiers are not guaranteed to be stable, so the saved query
        is replayed and matched on the normalised description.  More than one
        match means the monitor must ask the user again instead of guessing.
        """
        if selection.search_type is SearchType.SERVICE:
            candidates = await self._client.autocomplete_services(
                portal_session, query=selection.query_text, tax_code=tax_code
            )
            target = normalize_for_match(selection.service_name or "")
            matches = [c for c in candidates if normalize_for_match(c.description) == target]
            if not matches and selection.service_id:
                matches = [c for c in candidates if c.service_id == selection.service_id]
            if not matches:
                raise AmbiguousSearchResult(
                    "the saved service is no longer returned by the portal",
                    query=selection.query_text,
                )
            if len({c.service_id for c in matches}) > 1:
                raise AmbiguousSearchResult(
                    "the saved service now matches multiple portal results",
                    query=selection.query_text,
                )
            chosen = matches[0]
            return SearchSelection(
                search_type=SearchType.SERVICE,
                query_text=selection.query_text,
                service_id=chosen.service_id,
                service_name=chosen.description,
                specification_id=chosen.specification_id,
                preparation_note=chosen.preparation_note,
                online_bookable=chosen.online_bookable,
            )

        candidates_doctors = await self._client.autocomplete_doctors(
            portal_session, query=selection.query_text, tax_code=tax_code
        )
        target = normalize_for_match(selection.doctor_name or selection.doctor_id or "")
        doctor_matches = [
            c for c in candidates_doctors if normalize_for_match(c.description) == target
        ]
        if not doctor_matches and selection.doctor_id:
            doctor_matches = [c for c in candidates_doctors if c.raw_value == selection.doctor_id]
        if not doctor_matches:
            raise AmbiguousSearchResult(
                "the saved doctor is no longer returned by the portal",
                query=selection.query_text,
            )
        if len({c.raw_value for c in doctor_matches}) > 1:
            raise AmbiguousSearchResult(
                "the saved doctor now matches multiple portal results",
                query=selection.query_text,
            )
        chosen_doctor = doctor_matches[0]
        return SearchSelection(
            search_type=SearchType.DOCTOR,
            query_text=selection.query_text,
            doctor_id=chosen_doctor.raw_value,
            doctor_name=chosen_doctor.description,
            specification_id=selection.specification_id,
            service_id=selection.service_id,
            service_name=selection.service_name,
        )

    async def fetch_availability(
        self, flow: AvailabilityFlow, *, location_ids: Sequence[str] = ()
    ) -> PortalAvailability:
        """Read availability for the current wizard stage.

        The portal answers ``400`` when asked for availability without a
        location, so a service that exposes none is reported as not bookable
        online instead of failing as a technical error.
        """
        service_id = flow.selection.service_id or ""
        if not service_id and flow.locations:
            service_id = ""
        wanted = list(location_ids) or [item.location_id for item in flow.locations]
        if not wanted:
            raise ServiceNotBookableOnline(
                "the portal exposes no location for this selection",
                search_type=flow.selection.search_type.value,
            )
        availability = await self._client.fetch_availability(
            flow.portal_session, service_id=service_id, location_ids=wanted
        )
        flow.availability = availability
        if not flow.locations and availability.locations:
            flow.locations = availability.locations
        return availability

    async def collect(
        self,
        session: AsyncSession,
        account: PortalAccount,
        patient: Patient,
        selection: SearchSelection,
        slot_filter: SlotFilter,
        *,
        now: datetime,
    ) -> AvailabilityResult:
        """Run a full availability probe and apply the user filters."""
        async with self.availability_flow(session, account, patient, selection) as flow:
            requested = (
                ()
                if slot_filter.location_mode is LocationMode.ALL_DYNAMIC
                else tuple(slot_filter.location_ids)
            )
            availability = await self.fetch_availability(flow, location_ids=requested)
            unique = deduplicate(availability.slots)
            filtered = apply_filter(unique, slot_filter, now=now)
            return AvailabilityResult(
                locations=availability.locations or flow.locations,
                slots=tuple(filtered),
                total_before_filter=len(unique),
            )

    @staticmethod
    def ensure_any(result: AvailabilityResult) -> None:
        if not result.slots:
            raise NoAvailability("no slot matches the current filters")
