"""
Surface ops — infrastructure publish/update/revoke for AI-authored temporary web workspaces.

Not a channel. Not a surface intelligence engine.
AI authors HTML/CSS/JS (and optional lifetime). Infrastructure exposes securely.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


async def publish_surface(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    html = args.get("html") or args.get("raw") or args.get("body")
    if not html or not str(html).strip():
        return {"ok": False, "error": "html_required"}
    from wax.surfaces.service import SurfaceService

    lifetime = (
        args.get("preferred_lifetime_hours")
        or args.get("lifetime_hours")
        or args.get("hours")
        or args.get("ttl_hours")
    )
    svc = SurfaceService(session)
    return await svc.create(
        principal_id=principal_id,
        html=str(html),
        title=args.get("title"),
        description=args.get("description"),
        work_id=ctx.get("work_id"),
        preferred_lifetime_hours=float(lifetime) if lifetime is not None else None,
        lifecycle_intent=str(args.get("lifecycle_intent") or "temporary"),
        initial_state=args.get("initial_state") if isinstance(args.get("initial_state"), dict) else None,
    )


async def update_surface(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    principal_id = ctx.get("principal_id")
    surface_id = args.get("surface_id") or args.get("id")
    if not principal_id or not surface_id:
        return {"ok": False, "error": "surface_id_required"}
    from wax.surfaces.service import SurfaceService

    svc = SurfaceService(session)
    return await svc.update(
        surface_id=surface_id,
        principal_id=principal_id,
        html=args.get("html") or args.get("raw"),
        title=args.get("title"),
        description=args.get("description"),
        merge_state=args.get("merge_state") if isinstance(args.get("merge_state"), dict) else None,
        extend_hours=args.get("extend_hours") or args.get("lifetime_hours"),
    )


async def revoke_surface(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    principal_id = ctx.get("principal_id")
    surface_id = args.get("surface_id") or args.get("id")
    if not principal_id or not surface_id:
        return {"ok": False, "error": "surface_id_required"}
    from wax.surfaces.service import SurfaceService

    svc = SurfaceService(session)
    return await svc.revoke(surface_id, principal_id)


async def handle_publish_directive(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Dispatch publish channel: create | update | revoke (default create)."""
    action = str(args.get("action") or "create").strip().lower()
    if action in ("revoke", "delete", "close"):
        return await revoke_surface(session, args, ctx)
    if action in ("update", "revise", "patch"):
        return await update_surface(session, args, ctx)
    return await publish_surface(session, args, ctx)
