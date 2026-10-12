"""Structured JSON logging with mandatory redaction.

Every log record is emitted as a single JSON object.  Extra fields are passed
through :mod:`app.security.redaction` before serialisation, so a caller cannot
accidentally leak a password, a cookie or a tax code by attaching it to a log
record.
"""

from __future__ import annotations

import contextvars
import logging
import sys
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Final

import orjson

from app.security.redaction import redact_text, redact_value

_CORRELATION_ID: Final[contextvars.ContextVar[str | None]] = contextvars.ContextVar(
    "correlation_id", default=None
)
_SERVICE_NAME: Final[contextvars.ContextVar[str]] = contextvars.ContextVar(
    "service_name", default="app"
)

_RESERVED_ATTRS: Final[frozenset[str]] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


def new_correlation_id() -> str:
    """Generate a short correlation identifier safe to show to end users."""
    return uuid.uuid4().hex[:16]


def get_correlation_id() -> str | None:
    return _CORRELATION_ID.get()


@contextmanager
def correlation_scope(correlation_id: str | None = None) -> Iterator[str]:
    """Bind a correlation identifier for the duration of the block."""
    value = correlation_id or new_correlation_id()
    token = _CORRELATION_ID.set(value)
    try:
        yield value
    finally:
        _CORRELATION_ID.reset(token)


def set_service_name(service: str) -> None:
    _SERVICE_NAME.set(service)


class RedactingJsonFormatter(logging.Formatter):
    """Serialise log records as redacted JSON documents."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "service": _SERVICE_NAME.get(),
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }
        correlation_id = getattr(record, "correlation_id", None) or _CORRELATION_ID.get()
        if correlation_id:
            payload["correlation_id"] = correlation_id
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED_ATTRS and not key.startswith("_") and key != "correlation_id"
        }
        if extras:
            payload["context"] = redact_value(extras)
        if record.exc_info:
            exc_type = record.exc_info[0]
            exc_value = record.exc_info[1]
            payload["error_type"] = exc_type.__name__ if exc_type else "UnknownError"
            payload["error_message"] = redact_text(str(exc_value)) if exc_value else ""
        return orjson.dumps(payload).decode("utf-8")


class CorrelationFilter(logging.Filter):
    """Attach the ambient correlation identifier to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "correlation_id"):
            record.correlation_id = _CORRELATION_ID.get()
        return True


def configure_logging(*, service: str, level: str = "INFO") -> None:
    """Install the redacting JSON handler as the only root handler."""
    set_service_name(service)
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(RedactingJsonFormatter())
    handler.addFilter(CorrelationFilter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Third-party loggers must never dump raw HTTP traffic.
    for noisy in ("httpx", "httpcore", "aiomysql", "aiogram.event", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a module logger."""
    return logging.getLogger(name)


def log_event(
    logger: logging.Logger,
    level: int,
    event_code: str,
    message: str,
    context: Mapping[str, object] | None = None,
) -> None:
    """Emit a structured event with a stable machine readable code."""
    logger.log(level, message, extra={"event_code": event_code, **(context or {})})
