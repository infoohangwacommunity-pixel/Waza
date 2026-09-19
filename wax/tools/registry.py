"""
General tool registry.

Tools are capabilities, not educational modes.
The tutor decides when to use them. Nothing is subject-specific.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import Artifact, ScheduledAction
from wax.observability.logging import get_logger
from wax.scheduler.service import SchedulerService
from wax.terminal.executor import get_terminal

logger = get_logger(__name__)


ToolHandler = Callable[[AsyncSession, dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]]


async def handle_schedule_followup(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    hours = float(args.get("delay_hours") or 24)
    reason = args.get("reason") or "follow-up"
    payload = {
        "message_hint": args.get("message_hint"),
        "created_by": "tutor_tool",
    }
    sched = SchedulerService(session)
    action = await sched.schedule_in_hours(
        principal_id=principal_id,
        action_type="tutor_followup",
        hours=hours,
        reason=reason,
        payload=payload,
    )
    return {
        "ok": True,
        "scheduled_action_id": str(action.id),
        "execute_at": action.execute_at.isoformat(),
        "reason": reason,
    }


async def handle_create_artifact(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    kind = (args.get("kind") or "notes").strip()[:80]
    title = (args.get("title") or "Untitled").strip()[:500]
    content = args.get("content") or ""
    art = Artifact(
        id=uuid4(),
        principal_id=principal_id,
        work_id=ctx.get("work_id"),
        kind=kind,
        title=title,
        content=content,
        content_type="text/plain",
        size_bytes=len(content.encode("utf-8")),
        status="ready",
        structured={"created_via": "tutor_tool"},
    )
    session.add(art)
    await session.flush()
    return {
        "ok": True,
        "artifact_id": str(art.id),
        "kind": kind,
        "title": title,
        "size_bytes": art.size_bytes,
    }


async def handle_run_python(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    code = args.get("code") or ""
    if not code.strip():
        return {"ok": False, "error": "empty_code"}
    terminal = get_terminal()
    result = await terminal.run_python(code)
    return {
        "ok": result.success,
        "stdout": result.stdout[:8000],
        "stderr": result.stderr[:2000],
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
        "error": result.error,
    }


async def handle_present_choices(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """
    Tutor requests interactive choices for the current channel.
    Does not send itself — returns structured data for the delivery layer.
    """
    style = (args.get("style") or "buttons").lower()
    choices_raw = args.get("choices") or []
    choices = []
    for i, c in enumerate(choices_raw[:10]):
        if isinstance(c, str):
            choices.append({"id": f"opt_{i}", "title": c[:20], "description": None})
        elif isinstance(c, dict):
            choices.append(
                {
                    "id": str(c.get("id") or f"opt_{i}")[:256],
                    "title": str(c.get("title") or c.get("label") or f"Option {i}")[:20],
                    "description": (c.get("description") or None),
                }
            )
    if not choices:
        return {"ok": False, "error": "no_choices"}
    return {
        "ok": True,
        "style": "list" if style == "list" and len(choices) > 3 else "buttons",
        "choices": choices,
        "list_button_label": (args.get("list_button_label") or "Options")[:20],
        "prompt": args.get("prompt") or "",
    }

HANDLERS: dict[str, ToolHandler] = {
    "schedule_followup": handle_schedule_followup,
    "create_artifact": handle_create_artifact,
    "run_python": handle_run_python,
    "present_choices": handle_present_choices,
    "inspect_memories": handle_inspect_memories,
}


def parse_tool_args(raw: str | dict | None) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


async def execute_tool(
    session: AsyncSession,
    name: str,
    arguments: str | dict | None,
    ctx: dict[str, Any],
) -> dict[str, Any]:
    handler = HANDLERS.get(name)
    if not handler:
        return {"ok": False, "error": f"unknown_tool:{name}"}
    args = parse_tool_args(arguments)
    try:
        outcome = await handler(session, args, ctx)
        logger.info("tool_executed", tool=name, ok=outcome.get("ok"))
        return outcome
    except Exception as e:
        logger.exception("tool_failed", tool=name)
        return {"ok": False, "error": str(e)}


async def handle_inspect_memories(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Return a safe summary of what is remembered — for learner transparency."""
    from wax.memory.service import MemoryService

    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    svc = MemoryService(session)
    mems = await svc.list_for_user(principal_id, limit=int(args.get("limit") or 20))
    return {
        "ok": True,
        "count": len(mems),
        "memories": [
            {
                "id": str(m.id),
                "type": m.memory_type,
                "content": m.content,
                "confidence": m.confidence,
                "source": m.source,
            }
            for m in mems
        ],
    }


async def handle_manage_goal(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Create or update a goal generically — no educational taxonomy."""
    from datetime import datetime, timezone
    from wax.db.models import Goal

    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    action = (args.get("action") or "create").lower()
    title = (args.get("title") or "").strip()
    if action == "create":
        if not title:
            return {"ok": False, "error": "title_required"}
        g = Goal(
            id=uuid4(),
            principal_id=principal_id,
            title=title[:500],
            description=(args.get("description") or None),
            status="active",
            priority=int(args.get("priority") or 50),
        )
        session.add(g)
        await session.flush()
        return {"ok": True, "goal_id": str(g.id), "title": g.title, "status": g.status}
    goal_id = args.get("goal_id")
    if not goal_id:
        return {"ok": False, "error": "goal_id_required"}
    g = await session.get(Goal, goal_id)
    if not g or g.principal_id != principal_id:
        return {"ok": False, "error": "not_found"}
    if action == "complete":
        g.status = "completed"
        g.completed_at = datetime.now(timezone.utc)
    elif action == "pause":
        g.status = "paused"
    elif action == "abandon":
        g.status = "abandoned"
    elif action == "activate":
        g.status = "active"
    if args.get("title"):
        g.title = str(args["title"])[:500]
    if args.get("description") is not None:
        g.description = args.get("description")
    await session.flush()
    return {"ok": True, "goal_id": str(g.id), "status": g.status}


