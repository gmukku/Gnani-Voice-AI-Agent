"""Structured logging (assignment section 8).

JSON logs in every environment except local development, where a colourised
console renderer is easier to read. ``call_id`` and ``conversation_id`` are
bound to the context so every line emitted while handling a call carries them
without being passed around explicitly.
"""

from __future__ import annotations

import logging
import sys

import structlog

from app.config import get_settings


def configure_logging() -> None:
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )

    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: structlog.typing.Processor = (
        structlog.dev.ConsoleRenderer()
        if settings.environment == "development"
        else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


def bind_call_context(**kwargs: str | None) -> None:
    """Bind call identifiers for the remainder of this request/task."""
    structlog.contextvars.bind_contextvars(
        **{k: v for k, v in kwargs.items() if v is not None}
    )


def clear_call_context() -> None:
    structlog.contextvars.clear_contextvars()
