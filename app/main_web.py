"""FastAPI entrypoint for the secure connection form and the health endpoints.

The web surface exposes only what the product needs: the one-time credential
page, the optional single-use PDF link and the health probes.  There is no
administrative API here — administration lives entirely inside Telegram.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.composition import Container, build_container
from app.logging import get_logger
from app.version import APP_NAME, APP_VERSION
from app.web.routes import connect, download, health
from app.web.security import BodySizeLimitMiddleware, SecurityHeadersMiddleware

logger = get_logger(__name__)

TEMPLATES_DIRECTORY = Path(__file__).parent / "web" / "templates"


def create_app(container: Container | None = None) -> FastAPI:
    """Build the ASGI application with its dependencies attached to the state."""
    resolved = container or build_container(service_name="web")

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "web service starting",
            extra={"event_code": "WEB_STARTUP", "version": APP_VERSION},
        )
        yield
        await resolved.database.dispose()
        logger.info("web service stopped", extra={"event_code": "WEB_SHUTDOWN"})

    application = FastAPI(
        title=APP_NAME,
        version=APP_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    application.state.settings = resolved.settings
    application.state.database = resolved.database
    application.state.container = resolved
    application.state.account_service = resolved.accounts
    application.state.appointment_service = resolved.appointments
    application.state.templates = Jinja2Templates(directory=str(TEMPLATES_DIRECTORY))

    application.add_middleware(BodySizeLimitMiddleware)
    application.add_middleware(SecurityHeadersMiddleware, settings=resolved.settings)

    application.include_router(health.router)
    application.include_router(connect.router)
    application.include_router(download.router)

    @application.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        """The root page never lists accounts, tickets or any personal data."""
        templates: Jinja2Templates = request.app.state.templates
        return templates.TemplateResponse(
            request,
            "result.html",
            {
                "heading": "Assistente prenotazioni",
                "style": "notice",
                "message": "Questo servizio è privato e accessibile solo su invito.",
                "details": [],
            },
        )

    return application


def main() -> None:
    """Run the ASGI server; used by the container entrypoint."""
    import uvicorn

    uvicorn.run(
        "app.main_web:create_app",
        factory=True,
        # The reverse proxy is the only public listener; the container network
        # keeps this port private.
        host="0.0.0.0",
        port=8000,
        log_config=None,
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips="*",
        timeout_graceful_shutdown=20,
        workers=1,
    )


if __name__ == "__main__":
    main()
