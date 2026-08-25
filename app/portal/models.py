"""Adapter level data structures shared by the client and the services."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain.entities import PortalLocation, PortalPatient, PortalService


@dataclass(frozen=True, slots=True)
class PortalCredentials:
    """Portal credentials, decrypted for the shortest possible time."""

    email: str
    password: str


@dataclass(slots=True)
class WizardState:
    """Server side wizard position for one portal session.

    The portal keeps state between the ``/appointments/create`` POSTs, so the
    HTML of the current step and its CSRF token must be carried forward inside
    the very same session.
    """

    html: str = ""
    csrf_token: str = ""
    patients: tuple[PortalPatient, ...] = ()
    locations: tuple[PortalLocation, ...] = ()
    #: Services the chosen doctor performs; empty outside the doctor path.
    doctor_services: tuple[PortalService, ...] = ()
    session_fields: dict[str, str] = field(default_factory=dict)
    selected_tax_code: str | None = None
    stage: str = "INITIAL"


@dataclass(frozen=True, slots=True)
class PortalResponse:
    """A redacted view of an HTTP response used for diagnostics."""

    status_code: int
    url: str
    content_type: str
    body_length: int
    challenge: str | None = None
    messages: tuple[str, ...] = ()

    def as_context(self) -> dict[str, Any]:
        return {
            "status_code": self.status_code,
            "content_type": self.content_type,
            "body_length": self.body_length,
            "challenge": self.challenge or "",
        }
