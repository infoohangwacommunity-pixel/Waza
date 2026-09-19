"""Structured logging with correlation support."""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any

import structlog

from wax.config import get_settings

correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
principal_id_var: ContextVar[str | None] = ContextVar("principal_id", default=None)
work_id_var: ContextVar[str | None] = ContextVar("work_id", default=None)


def add_correlation(_, __, event_dict: dict[str, Any]) -> dict[str, Any]:
    if cid := correlation_id.get():
        event_dict["correlation_id"] = cid
    if pid := principal_id_var.get():
        event_dict["principal_id"] = pid
    if wid := work_id_var.get():
        event_dict["work_id"] = wid
    return event_dict


def setup_logging() -> None:
    settings = get_settings()
    shared = [
        structlog.contextvars.merge_contextvars,
        add_correlation,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if settings.enable_structured_logging:
        processors = shared + [structlog.processors.JSONRenderer()]
    else:
        processors = shared + [structlog.dev.ConsoleRenderer()]
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
    )


def get_logger(name: str = __name__) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
