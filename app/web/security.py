"""Security middleware and helpers for the public web surface."""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Awaitable, Callable
from hashlib import sha256
from typing import Final

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.config import Settings

CSRF_COOKIE_NAME: Final[str] = "opbg_csrf"
CSRF_FIELD_NAME: Final[str] = "csrf_token"
MAX_BODY_BYTES: Final[int] = 16 * 1024

#: The credential page loads no remote script, style, font or image.
CONTENT_SECURITY_POLICY: Final[str] = (
    "default-src 'none'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "form-action 'self'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'"
)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def hash_csrf(token: str) -> bytes:
    return sha256(token.encode("utf-8")).digest()


def verify_csrf(token: str, expected_hash: bytes | None) -> bool:
    if not token or expected_hash is None:
        return False
    return hmac.compare_digest(hash_csrf(token), expected_hash)


def set_csrf_cookie(response: Response, token: str, *, secure: bool, max_age: int) -> None:
    response.set_cookie(
        CSRF_COOKIE_NAME,
        token,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )


def clear_csrf_cookie(response: Response) -> None:
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds strict security headers and prevents caching of sensitive pages."""

    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        super().__init__(app)
        self._settings = settings

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
        response.headers["Pragma"] = "no-cache"
        if self._settings.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Rejects oversized request bodies before they reach a handler."""

    def __init__(self, app: ASGIApp, *, max_bytes: int = MAX_BODY_BYTES) -> None:
        super().__init__(app)
        self._max_bytes = max_bytes

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > self._max_bytes:
            return Response(status_code=413, content="Richiesta troppo grande")
        return await call_next(request)


def client_fingerprint(request: Request) -> str:
    """Derive a rate-limit subject without persisting the raw address."""
    forwarded = request.headers.get("x-forwarded-for", "")
    address = forwarded.split(",")[0].strip() if forwarded else ""
    if not address and request.client is not None:
        address = request.client.host
    return address or "unknown"


def origin_is_allowed(request: Request, settings: Settings) -> bool:
    """Validate the ``Origin``/``Referer`` header against the public base URL."""
    expected = settings.public_base_url.rstrip("/")
    origin = request.headers.get("origin")
    if origin:
        return origin.rstrip("/") == expected
    referer = request.headers.get("referer")
    if referer:
        return referer.rstrip("/").startswith(expected)
    # Some privacy-preserving clients strip both headers; the CSRF token and the
    # single-use ticket remain the primary defences in that case.
    return True
