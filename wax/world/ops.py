"""
World ops — single AI-facing path into the student's World.

AI request → secure process in World → observation.

No discover/acquire product surfaces. Package installs use the same path with
network_mode=pkg when the AI needs outbound package mirrors.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from wax.observability.logging import get_logger

logger = get_logger(__name__)


async def world_exec(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Run a command or script inside the principal's isolated World."""
    from wax.config import get_settings

    if not getattr(get_settings(), "isolation_enabled", True):
        return {"ok": False, "error": "world_exec_disabled"}
    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}

    from wax.world.manager import get_or_create_world
    from wax.world.exec import world_exec as _exec

    world = get_or_create_world(str(principal_id))
    argv = args.get("argv")
    script = args.get("script")
    command = args.get("command") or args.get("commands")
    if not argv and not script and command:
        body = str(command).strip()
        if body:
            argv = ["bash", "-lc", body]
    if not argv and not script:
        return {"ok": False, "error": "argv_or_script_required"}

    # Security: only none (default) or pkg (package mirrors). Not free internet.
    net = str(args.get("network_mode") or "none").strip().lower()
    if net not in ("none", "pkg"):
        net = "none"

    return await _exec(
        world,
        argv=list(argv) if argv else None,
        script=script,
        cwd_rel=str(args.get("cwd") or "workspace"),
        network_mode=net,
    )
