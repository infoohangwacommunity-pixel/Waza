"""Capability policy — infrastructure safety, not an intelligence tool menu."""

from __future__ import annotations

from typing import Any

from wax.config import get_settings
from wax.observability.logging import get_logger

logger = get_logger(__name__)


def code_execution_allowed() -> bool:
    settings = get_settings()
    return bool(getattr(settings, "allow_code_execution", True))


def external_network_allowed() -> bool:
    settings = get_settings()
    return bool(getattr(settings, "allow_external_network", True))


def world_exec_allowed(ctx: dict[str, Any] | None = None) -> tuple[bool, str | None]:
    settings = get_settings()
    if not getattr(settings, "allow_code_execution", True):
        return False, "code_execution_disabled"
    if ctx and ctx.get("principal_blocked"):
        return False, "principal_restricted"
    return True, None
