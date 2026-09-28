"""Interaction runtime ops — server-authoritative choices."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


async def present_choices(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    from wax.interaction.service import InteractionService

    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    style = (args.get("style") or "buttons").lower()
    choices_raw = args.get("choices") or []
    choices = []
    for i, c in enumerate(choices_raw[:10]):
        if isinstance(c, str):
            choices.append({"id": f"opt_{i}", "title": c[:64], "description": None})
        elif isinstance(c, dict):
            choices.append(
                {
                    "id": str(c.get("id") or f"opt_{i}")[:128],
                    "title": str(c.get("title") or c.get("label") or f"Option {i}")[:64],
                    "description": c.get("description"),
                }
            )
    if not choices:
        return {"ok": False, "error": "no_choices"}
    expires_in = args.get("expires_in_seconds")
    try:
        expires_in = int(expires_in) if expires_in is not None else None
    except (TypeError, ValueError):
        expires_in = None
    channel = (ctx.get("channel") or "telegram").lower()
    svc = InteractionService(session)
    ix = await svc.create(
        principal_id=principal_id,
        channel=channel,
        choices=choices,
        prompt=args.get("prompt") or "",
        style="list" if style == "list" and len(choices) > 3 else "buttons",
        work_id=ctx.get("work_id"),
        conversation_id=ctx.get("conversation_id"),
        expires_in_seconds=expires_in,
        metadata={
            "target_external_id": ctx.get("target_external_id"),
            "list_button_label": (args.get("list_button_label") or "Options")[:20],
        },
    )
    return {
        "ok": True,
        "interaction_id": str(ix.id),
        "style": ix.style,
        "choices": ix.choices,
        "expires_at": ix.expires_at.isoformat() if ix.expires_at else None,
    }
