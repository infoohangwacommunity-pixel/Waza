"""Capability policy — software enforces tool permissions, not prompts alone."""

from __future__ import annotations

from typing import Any

from wax.config import get_settings
from wax.tools.meta import (
    CODE_EXECUTION,
    EXTERNAL_NETWORK,
    tool_meta,
)
from wax.observability.logging import get_logger

logger = get_logger(__name__)


def tool_allowed(name: str, ctx: dict[str, Any] | None = None) -> tuple[bool, str | None]:
    """Return (allowed, reason_if_denied)."""
    settings = get_settings()
    meta = tool_meta(name)
    perms = set(meta.get("permissions") or [])

    if CODE_EXECUTION in perms and not getattr(settings, "allow_code_execution", True):
        return False, "code_execution_disabled"
    if EXTERNAL_NETWORK in perms and not getattr(settings, "allow_external_network", True):
        return False, "external_network_disabled"
    if not getattr(settings, "terminal_enabled", True) and name in (
        "run_python",
        "workspace_command",
    ):
        return False, "terminal_disabled"

    # Principal soft-block via ctx
    if ctx and ctx.get("principal_blocked"):
        if meta.get("risk") in ("high", "medium"):
            return False, "principal_restricted"

    return True, None
