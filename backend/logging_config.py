"""Structured JSON logging via structlog. Emits trace_id-aware records.

Imported once at app startup (FastAPI lifespan) and once per CLI entrypoint.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict


def _drop_color_codes(_: Any, __: str, event_dict: EventDict) -> EventDict:
    """Strip terminal colour codes when writing JSON to a file/log collector."""
    msg = event_dict.get("event")
    if isinstance(msg, str) and "\x1b[" in msg:
        # ANSI escape stripping, keep simple, this is a safety net.
        import re

        event_dict["event"] = re.sub(r"\x1b\[[0-9;]*m", "", msg)
    return event_dict


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    """Configure structlog + stdlib logging.

    Args:
        level: stdlib log level name.
        json_output: if True (production), emit JSON; else (dev), pretty console.
    """
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        _drop_color_codes,
    ]

    if json_output:
        shared_processors.append(structlog.processors.format_exc_info)
        shared_processors.append(structlog.processors.JSONRenderer())
    else:
        shared_processors.append(structlog.dev.ConsoleRenderer(colors=True))

    structlog.configure(
        processors=shared_processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a structlog logger. Use module __name__ as the typical argument."""
    return structlog.get_logger(name)


def bind_trace_id(trace_id: str) -> None:
    """Bind a trace_id to the current logging context (uses contextvars)."""
    structlog.contextvars.bind_contextvars(trace_id=trace_id)


def clear_trace_context() -> None:
    """Clear any bound contextvars at end of request."""
    structlog.contextvars.clear_contextvars()
