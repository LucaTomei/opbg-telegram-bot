"""Single-use HTTPS delivery of an appointment reminder PDF.

The PDF is fetched from the portal on demand, validated and streamed once.  It
is never written to disk and the link is consumed by the first successful
download.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import HTMLResponse

from app.db.repositories.accounts import DownloadTokenRepository
from app.db.repositories.base import utc_now
from app.db.repositories.bookings import AppointmentRepository
from app.db.session import Database
from app.domain.errors import PdfUnavailable, PortalError
from app.logging import correlation_scope, get_logger
from app.services.appointments import AppointmentService

logger = get_logger(__name__)
router = APIRouter(tags=["download"])


def _error(request: Request, message: str, *, status_code: int) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "error.html",
        {"heading": "Download non disponibile", "message": message, "correlation_id": ""},
        status_code=status_code,
    )


@router.get("/download/{token}")
async def download_pdf(request: Request, token: str) -> Response:
    """Deliver the reminder PDF exactly once for a valid token."""
    database: Database = request.app.state.database
    service: AppointmentService = request.app.state.appointment_service

    with correlation_scope():
        async with database.session() as session:
            tokens = DownloadTokenRepository(session)
            record = await tokens.get_by_token(token)
            if record is None or not record.is_usable(utc_now()):
                return _error(
                    request,
                    "Il link non è più valido. Richiedine uno nuovo dal bot.",
                    status_code=status.HTTP_404_NOT_FOUND,
                )
            appointment = await AppointmentRepository(session).get_any(record.appointment_id)
            if appointment is None or appointment.telegram_user_id != record.telegram_user_id:
                return _error(
                    request,
                    "L'appuntamento collegato non è più disponibile.",
                    status_code=status.HTTP_404_NOT_FOUND,
                )
            try:
                document = await service.download_pdf(session, appointment=appointment)
            except (PdfUnavailable, PortalError):
                logger.warning("PDF download failed", extra={"event_code": "PDF_DOWNLOAD_FAILED"})
                return _error(
                    request,
                    "Il promemoria non è disponibile in questo momento.",
                    status_code=status.HTTP_502_BAD_GATEWAY,
                )
            await tokens.consume(record)

    return Response(
        content=document.content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{document.filename}"',
            "Cache-Control": "no-store, no-cache, must-revalidate, private",
            "Content-Length": str(document.size),
        },
    )
