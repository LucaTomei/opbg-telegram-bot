"""Appointment synchronisation, cancellation and PDF delivery."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models.accounts import Patient, PortalAccount
from app.db.models.bookings import AppointmentCache
from app.db.models.users import TelegramUser
from app.db.repositories.accounts import (
    DownloadTokenRepository,
    PatientRepository,
    PortalAccountRepository,
)
from app.db.repositories.base import Page, utc_now
from app.db.repositories.bookings import AppointmentRepository, CancellationAttemptRepository
from app.domain.entities import PdfDocument, PortalAppointment
from app.domain.enums import AppointmentStatus
from app.domain.errors import (
    CancellationAmbiguous,
    ConflictError,
    NotFoundError,
    PdfUnavailable,
    PermissionDeniedError,
)
from app.domain.fingerprint import idempotency_key
from app.logging import get_logger, new_correlation_id
from app.portal.client import PortalClient
from app.services.locks import account_lock, appointment_lock
from app.services.portal_sessions import PortalSessionManager

logger = get_logger(__name__)

CANCEL_LOCK_TIMEOUT_SECONDS = 15


@dataclass(frozen=True, slots=True)
class SyncResult:
    created: int
    updated: int
    cancelled: int
    #: Appointments seen for the first time by a background synchronisation.
    detected: tuple[uuid.UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class CancellationResult:
    succeeded: bool
    ambiguous: bool
    detail: str
    duplicate: bool = False


class AppointmentService:
    """Reads, cancels and delivers appointments."""

    def __init__(
        self,
        settings: Settings,
        client: PortalClient,
        sessions: PortalSessionManager,
    ) -> None:
        self._settings = settings
        self._client = client
        self._sessions = sessions

    # --- synchronisation --------------------------------------------------
    async def sync_account(
        self,
        session: AsyncSession,
        account: PortalAccount,
        *,
        include_history: bool = False,
        now: datetime | None = None,
    ) -> SyncResult:
        """Refresh the local appointment cache for one portal account."""
        moment = now or utc_now()
        repository = AppointmentRepository(session)
        patients = PatientRepository(session)
        async with self._sessions.authenticated_session(session, account) as portal_session:
            current = await self._client.list_appointments(portal_session)
            history = (
                await self._client.list_appointment_history(portal_session)
                if include_history
                else ()
            )

        created = 0
        updated = 0
        detected: list[uuid.UUID] = []
        known_patients = await patients.list_for_account(account.id, only_active=False)
        for appointment in (*current, *history):
            patient_id = self._match_patient(appointment, known_patients)
            record, is_new = await repository.upsert(
                user_id=account.telegram_user_id,
                account_id=account.id,
                patient_id=patient_id,
                appointment=appointment,
                now=moment,
            )
            created += int(is_new)
            updated += int(not is_new)
            if is_new:
                detected.append(record.id)
        vanished = await repository.mark_missing(
            account_id=account.id,
            seen_external_ids=[item.external_id for item in current],
            now=moment,
        )
        account.last_sync_at = moment
        await PortalAccountRepository(session).flush()
        return SyncResult(
            created=created,
            updated=updated,
            cancelled=len(vanished),
            detected=tuple(detected),
        )

    @staticmethod
    def _match_patient(
        appointment: PortalAppointment, patients: Sequence[Patient]
    ) -> uuid.UUID | None:
        """Associate an appointment with a known patient by display name."""
        label = (appointment.patient_label or "").strip().casefold()
        if not label:
            return None
        for patient in patients:
            name = patient.display_name
            if name and name.strip().casefold() == label:
                return patient.id
        return None

    async def list_for_user(
        self,
        session: AsyncSession,
        user: TelegramUser,
        *,
        page: int,
        page_size: int,
        include_past: bool = False,
    ) -> Page[AppointmentCache]:
        return await AppointmentRepository(session).list_upcoming(
            user.id, page=page, page_size=page_size, include_past=include_past
        )

    async def require_appointment(
        self, session: AsyncSession, user: TelegramUser, appointment_id: uuid.UUID
    ) -> AppointmentCache:
        record = await AppointmentRepository(session).get_owned(appointment_id, user.id)
        if record is None:
            raise NotFoundError("appointment not found")
        return record

    # --- cancellation -----------------------------------------------------
    async def cancel(
        self,
        session: AsyncSession,
        *,
        user: TelegramUser,
        appointment: AppointmentCache,
        initiated_by_admin_id: uuid.UUID | None = None,
        now: datetime | None = None,
    ) -> CancellationResult:
        """Cancel an appointment once and verify the outcome on the portal."""
        moment = now or utc_now()
        if appointment.status is AppointmentStatus.CANCELLED:
            return CancellationResult(
                succeeded=True,
                ambiguous=False,
                detail="l'appuntamento risulta già disdetto",
                duplicate=True,
            )
        accounts = PortalAccountRepository(session)
        account = await accounts.get(appointment.portal_account_id)
        if account is None or not account.is_usable:
            raise PermissionDeniedError("the portal account is not usable")

        patients = PatientRepository(session)
        tax_code = await self._resolve_tax_code(patients, appointment)

        attempts = CancellationAttemptRepository(session)
        key = idempotency_key(
            account_id=account.id,
            patient_id=appointment.patient_id or appointment.id,
            fingerprint=appointment.portal_external_id,
            scope="cancel",
        )
        record, created = await attempts.start(
            user_id=user.id,
            account_id=account.id,
            appointment_id=appointment.id,
            idempotency_key=key,
            correlation_id=new_correlation_id(),
            initiated_by_admin_id=initiated_by_admin_id,
        )
        if not created:
            return CancellationResult(
                succeeded=record.status == "SUCCEEDED",
                ambiguous=record.status == "AMBIGUOUS",
                detail="richiesta di disdetta già registrata",
                duplicate=True,
            )

        async with (
            appointment_lock(session, appointment.id, timeout_seconds=CANCEL_LOCK_TIMEOUT_SECONDS),
            account_lock(session, account.id, timeout_seconds=CANCEL_LOCK_TIMEOUT_SECONDS),
        ):
            await AppointmentRepository(session).register_cancellation_attempt(appointment)
            async with self._sessions.authenticated_session(session, account) as portal_session:
                await self._client.cancel_appointment(
                    portal_session,
                    appointment_id=appointment.portal_external_id,
                    tax_code=tax_code,
                    cancel_path=appointment.cancel_path,
                )
                refreshed = await self._client.list_appointments(portal_session)

        still_present = next(
            (item for item in refreshed if item.external_id == appointment.portal_external_id),
            None,
        )
        repository = AppointmentRepository(session)
        if still_present is None:
            await repository.mark_cancelled(appointment, now=moment)
            await attempts.finish(record, status="SUCCEEDED")
            return CancellationResult(succeeded=True, ambiguous=False, detail="disdetta confermata")
        hint = (still_present.status_hint or "").casefold()
        if any(marker in hint for marker in ("annull", "cancel", "disdet")):
            await repository.mark_cancelled(appointment, now=moment)
            await attempts.finish(record, status="SUCCEEDED")
            return CancellationResult(succeeded=True, ambiguous=False, detail="disdetta confermata")
        await attempts.finish(record, status="AMBIGUOUS", error_code=CancellationAmbiguous.code)
        logger.error(
            "cancellation outcome is ambiguous",
            extra={
                "event_code": "CANCELLATION_AMBIGUOUS",
                "appointment_id": appointment.id.hex,
            },
        )
        return CancellationResult(
            succeeded=False,
            ambiguous=True,
            detail="esito incerto: l'appuntamento risulta ancora presente",
        )

    @staticmethod
    async def _resolve_tax_code(patients: PatientRepository, appointment: AppointmentCache) -> str:
        if appointment.patient_id is not None:
            patient = await patients.get(appointment.patient_id)
            if patient is not None and patient.external_tax_code:
                return patient.external_tax_code
        candidates = await patients.list_for_account(
            appointment.portal_account_id, only_active=False
        )
        label = (appointment.patient_label or "").strip().casefold()
        for patient in candidates:
            matches_label = (
                patient.display_name is not None
                and patient.display_name.strip().casefold() == label
            )
            if matches_label and patient.external_tax_code:
                return patient.external_tax_code
        if len(candidates) == 1 and candidates[0].external_tax_code:
            return candidates[0].external_tax_code
        raise ConflictError("the patient of this appointment could not be resolved")

    # --- PDF --------------------------------------------------------------
    async def download_pdf(
        self, session: AsyncSession, *, appointment: AppointmentCache
    ) -> PdfDocument:
        """Download the reminder PDF; it is never written to disk."""
        account = await PortalAccountRepository(session).get(appointment.portal_account_id)
        if account is None or not account.is_usable:
            raise PdfUnavailable("the portal account is not usable")
        async with self._sessions.authenticated_session(session, account) as portal_session:
            return await self._client.download_pdf(
                portal_session,
                appointment_id=appointment.portal_external_id,
                max_bytes=self._settings.pdf_max_bytes,
            )

    async def issue_download_link(
        self,
        session: AsyncSession,
        *,
        user: TelegramUser,
        appointment: AppointmentCache,
        issued_by_admin_id: uuid.UUID | None = None,
    ) -> str:
        """Create a single-use HTTPS link for the reminder PDF."""
        from app.security.tokens import generate_token

        token = generate_token()
        await DownloadTokenRepository(session).create(
            user_id=user.id,
            appointment_id=appointment.id,
            token=token,
            ttl_minutes=self._settings.pdf_link_ttl_minutes,
            issued_by_admin_id=issued_by_admin_id,
        )
        return self._settings.pdf_url(token)
