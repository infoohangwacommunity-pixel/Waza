"""Publish runtime ops — AI builds in World; infrastructure exposes securely."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


async def publish_surface(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    html = args.get("html")
    if not html or not str(html).strip():
        return {"ok": False, "error": "html_required"}
    from wax.surfaces.service import SurfaceService

    svc = SurfaceService(session)
    return await svc.create(
        principal_id=principal_id,
        html=str(html),
        title=args.get("title"),
        description=args.get("description"),
        work_id=ctx.get("work_id"),
        preferred_lifetime_hours=args.get("preferred_lifetime_hours"),
        lifecycle_intent=args.get("lifecycle_intent") or "temporary",
        initial_state=args.get("initial_state"),
    )


async def update_surface(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    principal_id = ctx.get("principal_id")
    surface_id = args.get("surface_id")
    if not principal_id or not surface_id:
        return {"ok": False, "error": "surface_id_required"}
    from wax.surfaces.service import SurfaceService

    svc = SurfaceService(session)
    return await svc.update(
        surface_id=surface_id,
        principal_id=principal_id,
        html=args.get("html"),
        title=args.get("title"),
        description=args.get("description"),
        merge_state=args.get("merge_state"),
        extend_hours=args.get("extend_hours"),
    )


async def revoke_surface(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    principal_id = ctx.get("principal_id")
    surface_id = args.get("surface_id")
    if not principal_id or not surface_id:
        return {"ok": False, "error": "surface_id_required"}
    from wax.surfaces.service import SurfaceService

    svc = SurfaceService(session)
    return await svc.revoke(surface_id, principal_id)
