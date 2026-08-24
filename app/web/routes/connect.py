"""One-time HTTPS form used to connect or re-authenticate a portal account.

Credentials never travel through Telegram: the bot only sends a single-use
HTTPS link, and this router verifies the ticket, the CSRF token, the origin and
the rate limit before contacting the portal.  Nothing is persisted before the
portal confirms the login.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request, Response, status
from fastapi.responses import HTMLResponse

from app.config import Settings
from app.db.models.accounts import ConnectTicket
from app.db.repositories.accounts import ConnectTicketRepository
from app.db.session import Database
from app.domain.enums import ConnectTicketPurpose
from app.domain.errors import (
    AlreadyConsumedError,
    ApplicationError,
    AuthenticationFailed,
    ConflictError,
    ExpiredError,
    NotFoundError,
    PortalCaptchaRequired,
    PortalError,
    RateLimitedError,
    ValidationError,
)
from app.logging import correlation_scope, get_logger
from app.services.accounts import AccountService
from app.web.security import (
    CSRF_COOKIE_NAME,
    client_fingerprint,
    hash_csrf,
    new_csrf_token,
    origin_is_allowed,
    set_csrf_cookie,
    verify_csrf,
)

logger = get_logger(__name__)
router = APIRouter(tags=["connect"])

GENERIC_LOGIN_ERROR = (
    "Credenziali non valide oppure account non disponibile. Controlla i dati e riprova."
)


def _templates(request: Request) -> object:
    return request.app.state.templates


def _render(
    request: Request, template: str, context: dict[str, object], *, status_code: int = 200
) -> HTMLResponse:
    templates = _templates(request)
    return templates.TemplateResponse(  # type: ignore[no-any-return, attr-defined]
        request, template, context, status_code=status_code
    )


def _error_page(
    request: Request, message: str, *, status_code: int, correlation_id: str = ""
) -> HTMLResponse:
    return _render(
        request,
        "error.html",
        {
            "heading": "Collegamento non disponibile",
            "message": message,
            "correlation_id": correlation_id,
        },
        status_code=status_code,
    )


def _heading(ticket: ConnectTicket) -> str:
    return (
        "Aggiorna le credenziali"
        if ticket.purpose is ConnectTicketPurpose.REAUTH
        else "Collega un account del portale"
    )


@router.get("/connect/{token}", response_class=HTMLResponse)
async def show_form(request: Request, token: str) -> Response:
    """Render the credential form for a valid, unconsumed ticket."""
    database: Database = request.app.state.database
    settings: Settings = request.app.state.settings
    service: AccountService = request.app.state.account_service

    async with database.session() as session:
        try:
            await service.enforce_connect_rate_limit(
                session, scope="ip", subject=client_fingerprint(request)
            )
            ticket = await service.load_ticket(session, token)
        except RateLimitedError:
            return _error_page(
                request,
                "Troppi tentativi. Riprova più tardi.",
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        except (NotFoundError, ExpiredError, AlreadyConsumedError):
            return _error_page(
                request,
                "Il collegamento non è più valido. Richiedine uno nuovo dal bot.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        csrf_token = new_csrf_token()
        ticket.csrf_hash = hash_csrf(csrf_token)
        await ConnectTicketRepository(session).flush()

    response = _render(
        request,
        "connect.html",
        {
            "heading": _heading(ticket),
            "action": f"/connect/{token}",
            "csrf_token": csrf_token,
            "expires_in_minutes": settings.connect_ticket_ttl_minutes,
            "suggested_label": ticket.suggested_label or "",
            "error": None,
        },
    )
    set_csrf_cookie(
        response,
        csrf_token,
        secure=settings.is_production,
        max_age=settings.connect_ticket_ttl_minutes * 60,
    )
    return response


@router.post("/connect/{token}", response_class=HTMLResponse)
async def submit_form(
    request: Request,
    token: str,
    label: str = Form(default=""),
    email: str = Form(default=""),
    password: str = Form(default=""),
    csrf_token: str = Form(default="", alias="csrf_token"),
) -> Response:
    """Verify the credentials against the portal and store them encrypted."""
    database: Database = request.app.state.database
    settings: Settings = request.app.state.settings
    service: AccountService = request.app.state.account_service

    with correlation_scope() as correlation_id:
        if not origin_is_allowed(request, settings):
            return _error_page(
                request,
                "Richiesta non valida.",
                status_code=status.HTTP_403_FORBIDDEN,
                correlation_id=correlation_id,
            )
        cookie_token = request.cookies.get(CSRF_COOKIE_NAME, "")
        async with database.session() as session:
            try:
                await service.enforce_connect_rate_limit(
                    session, scope="ip", subject=client_fingerprint(request)
                )
                ticket = await service.load_ticket(session, token)
            except RateLimitedError:
                return _error_page(
                    request,
                    "Troppi tentativi. Riprova più tardi.",
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    correlation_id=correlation_id,
                )
            except (NotFoundError, ExpiredError, AlreadyConsumedError):
                return _error_page(
                    request,
                    "Il collegamento non è più valido. Richiedine uno nuovo dal bot.",
                    status_code=status.HTTP_404_NOT_FOUND,
                    correlation_id=correlation_id,
                )

            if not verify_csrf(csrf_token, ticket.csrf_hash) or csrf_token != cookie_token:
                await ConnectTicketRepository(session).register_failure(ticket)
                return _error_page(
                    request,
                    "Sessione scaduta. Riapri il collegamento dal bot.",
                    status_code=status.HTTP_403_FORBIDDEN,
                    correlation_id=correlation_id,
                )

            try:
                account = await service.complete_connection(
                    session,
                    ticket=ticket,
                    label=label,
                    email=email,
                    password=password,
                )
            except ValidationError as exc:
                return _form_with_error(request, token, settings, ticket, exc.detail)
            except ConflictError:
                # The ticket is consumed by the service in this case, so the
                # form cannot be re-submitted: show a terminal message instead.
                return _error_page(
                    request,
                    "Questo account è già collegato al tuo profilo Telegram.",
                    status_code=status.HTTP_409_CONFLICT,
                    correlation_id=correlation_id,
                )
            except AuthenticationFailed:
                # The same message is returned for an unknown account and for a
                # wrong password so the form cannot be used for enumeration.
                logger.warning(
                    "connect form login failed",
                    extra={"event_code": "CONNECT_LOGIN_FAILED"},
                )
                return _form_with_error(request, token, settings, ticket, GENERIC_LOGIN_ERROR)
            except PortalCaptchaRequired:
                return _error_page(
                    request,
                    "Il portale richiede una verifica manuale. Accedi dal sito ufficiale "
                    "e riprova più tardi.",
                    status_code=status.HTTP_409_CONFLICT,
                    correlation_id=correlation_id,
                )
            except PortalError:
                return _error_page(
                    request,
                    "Il portale non è raggiungibile in questo momento. Riprova più tardi.",
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    correlation_id=correlation_id,
                )
            except ApplicationError:
                return _error_page(
                    request,
                    "Operazione non disponibile.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                    correlation_id=correlation_id,
                )

            details = [f"Etichetta: {account.label}"]

    response = _render(
        request,
        "result.html",
        {
            "heading": "Account collegato",
            "style": "success",
            "message": "Le credenziali sono state verificate e salvate in modo cifrato.",
            "details": details,
        },
    )
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")
    return response


def _form_with_error(
    request: Request,
    token: str,
    settings: Settings,
    ticket: ConnectTicket,
    message: str,
) -> HTMLResponse:
    """Re-render the form with a fresh CSRF token after a recoverable error."""
    csrf_token = new_csrf_token()
    ticket.csrf_hash = hash_csrf(csrf_token)
    response = _render(
        request,
        "connect.html",
        {
            "heading": _heading(ticket),
            "action": f"/connect/{token}",
            "csrf_token": csrf_token,
            "expires_in_minutes": settings.connect_ticket_ttl_minutes,
            "suggested_label": ticket.suggested_label or "",
            "error": message or GENERIC_LOGIN_ERROR,
        },
        status_code=status.HTTP_400_BAD_REQUEST,
    )
    set_csrf_cookie(
        response,
        csrf_token,
        secure=settings.is_production,
        max_age=settings.connect_ticket_ttl_minutes * 60,
    )
    return response
