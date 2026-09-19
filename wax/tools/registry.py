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
    result = await terminal.run_python(
        code,
        principal_id=ctx.get("principal_id"),
        work_id=ctx.get("work_id"),
    )
    return {
        "ok": result.success,
        "stdout": result.stdout[:8000],
        "stderr": result.stderr[:2000],
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
        "error": result.error,
        "cwd": result.cwd,
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




async def handle_forget_memory(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    from wax.memory.service import MemoryService

    principal_id = ctx.get("principal_id")
    memory_id = args.get("memory_id")
    if not principal_id or not memory_id:
        return {"ok": False, "error": "memory_id_required"}
    ok = await MemoryService(session).forget(principal_id, memory_id)
    return {"ok": ok, "memory_id": memory_id}


HANDLERS["forget_memory"] = handle_forget_memory


async def handle_fetch_inbound_media(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Download channel media into the principal workspace."""
    from wax.messaging.media import fetch_whatsapp_media, fetch_telegram_media

    principal_id = ctx.get("principal_id")
    channel = (args.get("channel") or ctx.get("channel") or "").lower()
    media_id = args.get("media_id")
    if not principal_id or not media_id:
        return {"ok": False, "error": "principal_and_media_id_required"}
    if channel == "whatsapp":
        return await fetch_whatsapp_media(media_id, principal_id, args.get("filename"))
    if channel == "telegram":
        return await fetch_telegram_media(media_id, principal_id, args.get("filename"))
    return {"ok": False, "error": f"unsupported_channel:{channel}"}


async def handle_list_workspace(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    from wax.terminal.workspace import principal_workspace, list_files

    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    sub = (args.get("subdir") or "media").strip().lstrip("/")
    base = principal_workspace(principal_id)
    path = base / sub if sub else base
    if not str(path.resolve()).startswith(str(base.resolve())):
        return {"ok": False, "error": "path_escape"}
    files = list_files(path, limit=int(args.get("limit") or 40))
    return {"ok": True, "cwd": str(path), "files": files}


async def handle_inspect_media(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    from wax.terminal.executor import get_terminal

    path = args.get("path")
    if not path:
        return {"ok": False, "error": "path_required"}
    terminal = get_terminal()
    result = await terminal.inspect_media_file(path, principal_id=ctx.get("principal_id"))
    return {
        "ok": result.success,
        "stdout": result.stdout[:8000],
        "stderr": result.stderr[:1500],
        "error": result.error,
        "cwd": result.cwd,
    }


async def handle_workspace_command(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Run one allowed command inside the learner workspace."""
    from wax.terminal.executor import get_terminal

    line = (args.get("command") or "").strip()
    if not line:
        return {"ok": False, "error": "command_required"}
    terminal = get_terminal()
    result = await terminal.run_shell_line(
        line,
        principal_id=ctx.get("principal_id"),
        work_id=ctx.get("work_id"),
    )
    return {
        "ok": result.success,
        "stdout": result.stdout[:8000],
        "stderr": result.stderr[:2000],
        "exit_code": result.exit_code,
        "error": result.error,
        "cwd": result.cwd,
    }


HANDLERS["fetch_inbound_media"] = handle_fetch_inbound_media
HANDLERS["list_workspace"] = handle_list_workspace
HANDLERS["inspect_media"] = handle_inspect_media
HANDLERS["workspace_command"] = handle_workspace_command



async def handle_describe_image(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Multimodal fallback — use only when local OCR/inspect is not enough."""
    from wax.tools.multimodal import describe_local_image

    path = args.get("path")
    if not path:
        return {"ok": False, "error": "path_required"}
    return await describe_local_image(path, args.get("question"))


async def handle_list_artifacts(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    from sqlalchemy import select
    from wax.db.models import Artifact

    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    stmt = (
        select(Artifact)
        .where(Artifact.principal_id == principal_id, Artifact.status == "ready")
        .order_by(Artifact.created_at.desc())
        .limit(int(args.get("limit") or 15))
    )
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    return {
        "ok": True,
        "artifacts": [
            {
                "id": str(a.id),
                "kind": a.kind,
                "title": a.title,
                "size_bytes": a.size_bytes,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in rows
        ],
    }


async def handle_read_artifact(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    from wax.db.models import Artifact

    principal_id = ctx.get("principal_id")
    artifact_id = args.get("artifact_id")
    if not principal_id or not artifact_id:
        return {"ok": False, "error": "artifact_id_required"}
    art = await session.get(Artifact, artifact_id)
    if not art or art.principal_id != principal_id:
        return {"ok": False, "error": "not_found"}
    return {
        "ok": True,
        "id": str(art.id),
        "kind": art.kind,
        "title": art.title,
        "content": (art.content or "")[:20000],
    }


HANDLERS["describe_image"] = handle_describe_image
HANDLERS["list_artifacts"] = handle_list_artifacts
HANDLERS["read_artifact"] = handle_read_artifact


async def handle_write_workspace_file(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Write a text file into the learner workspace for the AI to build on."""
    from wax.terminal.workspace import principal_workspace, safe_write_bytes

    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    filename = (args.get("filename") or "note.txt").strip()
    content = args.get("content") or ""
    subdir = (args.get("subdir") or "out").strip().lstrip("/") or "out"
    base = principal_workspace(principal_id)
    dest_dir = base / subdir
    if not str(dest_dir.resolve()).startswith(str(base.resolve())):
        return {"ok": False, "error": "path_escape"}
    path = safe_write_bytes(dest_dir, filename, content.encode("utf-8"))
    return {"ok": True, "path": str(path), "size": path.stat().st_size}


async def handle_read_workspace_file(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    from pathlib import Path
    from wax.terminal.workspace import principal_workspace

    principal_id = ctx.get("principal_id")
    path = args.get("path")
    if not principal_id or not path:
        return {"ok": False, "error": "path_required"}
    base = principal_workspace(principal_id)
    p = Path(path)
    if not p.is_file():
        return {"ok": False, "error": "not_found"}
    if not str(p.resolve()).startswith(str(base.resolve())):
        return {"ok": False, "error": "path_escape"}
    data = p.read_text(encoding="utf-8", errors="replace")
    return {"ok": True, "path": str(p), "content": data[:30000]}


async def handle_schedule_continuous(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """
    Schedule a series of follow-ups (e.g. daily reminders, multi-day practice).
    Not a hardcoded student routine — tutor decides cadence and content hints.
    """
    from wax.scheduler.service import SchedulerService

    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    hours_list = args.get("hours_from_now") or args.get("delays_hours") or [24]
    if isinstance(hours_list, (int, float)):
        hours_list = [hours_list]
    reason = args.get("reason") or "ongoing follow-up"
    hint = args.get("message_hint") or reason
    sched = SchedulerService(session)
    created = []
    for h in list(hours_list)[:12]:
        try:
            hours = float(h)
        except (TypeError, ValueError):
            continue
        action = await sched.schedule_in_hours(
            principal_id=principal_id,
            action_type="tutor_followup",
            hours=max(0.05, hours),
            reason=reason,
            payload={"message_hint": hint, "series": True},
        )
        created.append({"id": str(action.id), "hours": hours, "at": action.execute_at.isoformat()})
    return {"ok": True, "scheduled": created, "count": len(created)}


HANDLERS["write_workspace_file"] = handle_write_workspace_file
HANDLERS["read_workspace_file"] = handle_read_workspace_file
HANDLERS["schedule_continuous"] = handle_schedule_continuous


async def handle_start_activity(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    from wax.work.activities import ActivityService

    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    duration = args.get("duration_seconds") or args.get("duration_minutes")
    if duration is not None and args.get("duration_minutes") and not args.get("duration_seconds"):
        duration = int(float(args["duration_minutes"]) * 60)
    elif duration is not None:
        duration = int(float(duration))
    act = await ActivityService(session).start(
        principal_id=principal_id,
        kind=(args.get("kind") or "practice")[:80],
        objective=args.get("objective"),
        duration_seconds=duration,
        content={"items": args.get("items") or [], "notes": args.get("notes")},
        conversation_id=ctx.get("conversation_id"),
        work_id=ctx.get("work_id"),
    )
    return {
        "ok": True,
        "activity_id": str(act.id),
        "kind": act.kind,
        "status": act.status,
        "ends_at": act.ends_at.isoformat() if act.ends_at else None,
    }


async def handle_complete_activity(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    from wax.work.activities import ActivityService

    aid = args.get("activity_id")
    if not aid:
        return {"ok": False, "error": "activity_id_required"}
    act = await ActivityService(session).complete(aid, outcome=args.get("outcome") or {})
    if not act:
        return {"ok": False, "error": "not_found"}
    return {"ok": True, "activity_id": str(act.id), "status": act.status}


async def handle_update_concept_state(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    from wax.knowledge.graph import KnowledgeGraphService

    principal_id = ctx.get("principal_id")
    label = args.get("concept") or args.get("concept_label")
    if not principal_id or not label:
        return {"ok": False, "error": "concept_required"}
    kg = KnowledgeGraphService(session)
    state = await kg.update_learner_state(
        principal_id,
        label,
        mastery_delta=float(args.get("mastery_delta") or 0.0),
        status=args.get("status"),
        note=args.get("note"),
        evidence_item={"source": "tutor_tool"},
    )
    if args.get("related_concept") and args.get("relation_type"):
        await kg.relate(label, args["related_concept"], args["relation_type"])
    return {
        "ok": True,
        "concept": label,
        "mastery": state.mastery,
        "status": state.status,
        "confidence": state.confidence,
    }


HANDLERS["start_activity"] = handle_start_activity
HANDLERS["complete_activity"] = handle_complete_activity
HANDLERS["update_concept_state"] = handle_update_concept_state
