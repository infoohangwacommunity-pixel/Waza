"""
World runtime ops — AI hands inside the student's isolated persistent environment.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from wax.observability.logging import get_logger

logger = get_logger(__name__)


async def world_discover(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    from wax.world.manager import get_or_create_world
    from wax.world.discover import discover

    world = get_or_create_world(str(principal_id))
    sections = args.get("sections")
    if isinstance(sections, str):
        sections = [s.strip() for s in sections.split(",") if s.strip()]
    try:
        info = discover(world, sections=sections)
        return info if isinstance(info, dict) else {"ok": True, "data": info}
    except Exception as e:
        logger.exception("world_discover_failed")
        return {"ok": False, "error": str(e)[:500]}


async def world_exec(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
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
        # Free-form body from agent: general-purpose shell inside the World.
        # No binary allowlist — isolation enforces security, not task type.
        body = str(command).strip()
        if body:
            argv = ["bash", "-lc", body]
    if not argv and not script:
        return {"ok": False, "error": "argv_or_script_required"}
    return await _exec(
        world,
        argv=list(argv) if argv else None,
        script=script,
        cwd_rel=str(args.get("cwd") or "workspace"),
        network_mode=str(args.get("network_mode") or "none"),
        budget_class=str(args.get("budget_class") or "interactive"),
    )


async def world_acquire(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    name = (args.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "name_required"}
    from wax.world.manager import get_or_create_world
    from wax.world.acquire import acquire
    from wax.world.providers.base import AcquireRequest

    world = get_or_create_world(str(principal_id))
    kind = (args.get("kind") or "python_package").strip()
    version_spec = args.get("version_spec")
    try:
        req = AcquireRequest(kind=kind, name=name, version_spec=version_spec)
        return await acquire(world, req)
    except Exception as e:
        logger.exception("world_acquire_failed")
        return {"ok": False, "error": str(e)[:500]}


async def world_files(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    from wax.world.manager import get_or_create_world
    from wax.world import files as wfiles

    world = get_or_create_world(str(principal_id))
    action = (args.get("action") or "list").lower()
    path = args.get("path") or "workspace"
    try:
        if action == "list":
            return wfiles.list_files(world, path)
        if action == "read":
            return wfiles.read_file(world, path)
        if action == "write":
            content = args.get("content")
            if content is None:
                return {"ok": False, "error": "content_required"}
            return wfiles.write_file(world, path, content)
        if action == "delete":
            return wfiles.delete_file(world, path)
        return {"ok": False, "error": f"unknown_action:{action}"}
    except Exception as e:
        logger.exception("world_files_failed")
        return {"ok": False, "error": str(e)[:500]}
