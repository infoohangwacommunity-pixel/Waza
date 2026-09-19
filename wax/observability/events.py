"""Lightweight structured event helpers for correlation."""

from __future__ import annotations

from typing import Any

from wax.observability.logging import get_logger

logger = get_logger("wax.events")


def emit(event: str, **fields: Any) -> None:
    logger.info(event, **fields)
