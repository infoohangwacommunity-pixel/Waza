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
from wax.tools.meta import tool_meta
from wax.security.policy import tool_allowed
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
    """Create durable artifact (txt/pdf). Does not claim channel delivery succeeded."""
    from wax.artifacts.generate import generate_bytes
    from wax.artifacts.storage import store_bytes
    from wax.artifacts.access import make_download_token, public_download_url

    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    kind = (args.get("kind") or "notes").strip()[:80]
    title = (args.get("title") or "Untitled").strip()[:500]
    content = args.get("content") or ""
    fmt = (args.get("format") or args.get("file_format") or "txt").strip().lower()
    if fmt not in ("txt", "pdf", "text", "plain"):
        fmt = "txt"
    if fmt in ("text", "plain"):
        fmt = "txt"

    data, content_type, fmt = generate_bytes(fmt, title, content)
    art_id = uuid4()
    ext = "pdf" if fmt == "pdf" else "txt"
    uri = store_bytes(principal_id, f"{art_id}.{ext}", data, content_type=content_type)

    art = Artifact(
        id=art_id,
        principal_id=principal_id,
        work_id=ctx.get("work_id"),
        kind=kind,
        title=title,
        content=content if fmt == "txt" else (content[:2000] if content else ""),
        content_type=content_type,
        size_bytes=len(data),
        status="ready",
        structured={
            "created_via": "tutor_tool",
            "format": fmt,
            "storage_uri": uri,
            "delivery_available": True,
            "delivered": False,
        },
    )
    session.add(art)
    await session.flush()
    token = make_download_token(str(art.id), str(principal_id))
    download_url = public_download_url(str(art.id), token)
    logger.info(
        "artifact_created",
        artifact_id=str(art.id),
        principal_id=str(principal_id),
        format=fmt,
        size_bytes=len(data),
    )
    return {
        "ok": True,
        "status": "ready",
        "artifact_id": str(art.id),
        "kind": kind,
        "title": title,
        "format": fmt,
        "content_type": content_type,
        "size_bytes": len(data),
        "storage_uri": uri,
        "download_url": download_url,
        "delivery_available": True,
        "delivered": False,
        "note": "Artifact stored. Channel delivery is separate — do not tell the learner it was sent until delivery succeeds.",
    }


async def handle_run_python(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Deprecated name — executes Python inside the learner World runtime, not the WAX app."""
    code = args.get("code") or ""
    if not code.strip():
        return {"ok": False, "error": "empty_code"}
    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    from wax.world.manager import get_or_create_world
    from wax.world.exec import world_exec

    world = get_or_create_world(str(principal_id))
    return await world_exec(world, script=code, budget_class="interactive")


async def handle_present_choices(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """
    Tutor requests interactive choices. Creates a durable Interaction row.
    Delivery layer renders; consume/expire is server-authoritative.
    """
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
                    "description": (c.get("description") or None),
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
        activity_id=ctx.get("activity_id"),
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
        "list_button_label": (args.get("list_button_label") or "Options")[:20],
        "prompt": args.get("prompt") or "",
        "expires_at": ix.expires_at.isoformat() if ix.expires_at else None,
        "expires_in_seconds": expires_in,
    }

HANDLERS: dict[str, ToolHandler] = {}

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
    """Execute tool. Side-effecting tools are idempotent per work+args hash."""
    import hashlib
    import json
    from wax.db.models import Work

    handler = HANDLERS.get(name)
    if not handler:
        return {"ok": False, "error": f"unknown_tool:{name}"}
    meta = tool_meta(name)
    allowed, deny_reason = tool_allowed(name, ctx)
    if not allowed:
        logger.warning("tool_denied_by_policy", tool=name, reason=deny_reason)
        return {"ok": False, "error": f"policy_denied:{deny_reason}"}
    logger.info(
        "tool_invoke",
        tool=name,
        risk=meta.get("risk"),
        permissions=meta.get("permissions"),
    )
    args = parse_tool_args(arguments)
    side_effect = name in {
        "schedule_followup",
        "schedule_continuous",
        "create_artifact",
        "create_assessment",
        "write_workspace_file",
        "start_activity",
        "manage_goal",
    }
    args_hash = hashlib.sha256(
        json.dumps({"name": name, "args": args}, sort_keys=True, default=str).encode()
    ).hexdigest()[:24]
    work_id = ctx.get("work_id")

    if side_effect and work_id:
        work = await session.get(Work, work_id)
        if work is not None:
            meta = dict(work.result_payload or {})
            done = dict(meta.get("_tool_idem") or {})
            if args_hash in done:
                logger.info("tool_idempotent_hit", tool=name, hash=args_hash)
                return done[args_hash]

    try:
        outcome = await handler(session, args, ctx)
        if side_effect and work_id:
            work = await session.get(Work, work_id)
            if work is not None:
                meta = dict(work.result_payload or {})
                done = dict(meta.get("_tool_idem") or {})
                done[args_hash] = outcome if isinstance(outcome, dict) else {"ok": True}
                meta["_tool_idem"] = done
                work.result_payload = meta
                await session.flush()
        logger.info(
            "tool_executed",
            tool=name,
            ok=outcome.get("ok") if isinstance(outcome, dict) else None,
        )
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
    """Probe media and optionally extract text (OCR/PDF). Does not auto-transcribe."""
    from wax.terminal.executor import get_terminal
    from wax.terminal.workspace import principal_workspace
    from pathlib import Path as P

    path = args.get("path") or ctx.get("local_media_path")
    if not path:
        return {"ok": False, "error": "path_required"}
    principal_id = ctx.get("principal_id")
    if principal_id:
        base = principal_workspace(principal_id)
        try:
            resolved = P(path).resolve()
            if not str(resolved).startswith(str(base.resolve())):
                return {"ok": False, "error": "path_escape"}
        except Exception:
            return {"ok": False, "error": "invalid_path"}
    # Inspection-only by default; OCR/PDF extraction must be explicit.
    extract_text = bool(args.get("extract_text") or False)
    max_pages = args.get("max_pages")
    terminal = get_terminal()
    result = await terminal.inspect_media_file(
        path,
        principal_id=principal_id,
        extract_text=bool(extract_text),
        max_pages=int(max_pages) if max_pages is not None else None,
    )
    out: dict[str, Any] = {
        "ok": result.success,
        "stdout": result.stdout[:8000],
        "stderr": result.stderr[:1500],
        "error": result.error,
        "cwd": result.cwd,
    }
    if result.structured:
        out["probe"] = result.structured.get("probe")
        out["evidence"] = result.structured.get("evidence")
        out["capabilities"] = (result.structured.get("probe") or {}).get("capability_list")
        out["note"] = result.structured.get("note")
    return out


async def handle_workspace_command(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Deprecated — use world_exec. Runs argv in World isolation (no binary allowlist)."""
    import shlex
    from wax.world.manager import get_or_create_world
    from wax.world.exec import world_exec

    line = (args.get("command") or "").strip()
    if not line:
        return {"ok": False, "error": "command_required"}
    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    try:
        argv = shlex.split(line)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    world = get_or_create_world(str(principal_id))
    return await world_exec(world, argv=argv, budget_class="interactive")


HANDLERS["fetch_inbound_media"] = handle_fetch_inbound_media
HANDLERS["list_workspace"] = handle_list_workspace
HANDLERS["inspect_media"] = handle_inspect_media
HANDLERS["workspace_command"] = handle_workspace_command



async def handle_describe_image(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Multimodal fallback — use only when local OCR/inspect is not enough."""
    from pathlib import Path as P
    from wax.tools.multimodal import describe_local_image
    from wax.terminal.workspace import principal_workspace

    path = (args.get("path") or "").strip()
    if not path:
        return {"ok": False, "error": "path_required"}
    principal_id = ctx.get("principal_id")
    if principal_id:
        base = principal_workspace(principal_id)
        try:
            resolved = P(path).resolve()
            if not str(resolved).startswith(str(base.resolve())):
                return {"ok": False, "error": "path_escape"}
        except Exception:
            return {"ok": False, "error": "invalid_path"}
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


async def handle_create_assessment(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    from wax.assessment.service import AssessmentService

    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    items = args.get("items") or []
    if not items:
        return {"ok": False, "error": "items_required"}
    duration = args.get("duration_seconds")
    if duration is None and args.get("duration_minutes"):
        duration = int(float(args["duration_minutes"]) * 60)
    timed = bool(args.get("timed") or duration)
    assessment = await AssessmentService(session).create(
        principal_id=principal_id,
        title=args.get("title") or "Practice",
        objective=args.get("objective"),
        items=items,
        timed=timed,
        duration_seconds=int(duration) if duration else None,
        conversation_id=ctx.get("conversation_id"),
        one_at_a_time=bool(args.get("one_at_a_time", True)),
    )
    attempt = await AssessmentService(session).start_attempt(assessment.id, principal_id)
    nxt = await AssessmentService(session).next_item(assessment.id, attempt.id)
    return {
        "ok": True,
        "assessment_id": str(assessment.id),
        "attempt_id": str(attempt.id),
        "next_item": (
            {
                "item_id": str(nxt.id),
                "prompt": nxt.prompt,
                "item_type": nxt.item_type,
                "options": nxt.options,
            }
            if nxt
            else None
        ),
    }


async def handle_submit_assessment_answer(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    from wax.assessment.service import AssessmentService

    attempt_id = args.get("attempt_id")
    item_id = args.get("item_id")
    if not attempt_id or not item_id:
        return {"ok": False, "error": "attempt_id_and_item_id_required"}
    svc = AssessmentService(session)
    resp = await svc.submit_response(
        attempt_id=attempt_id,
        item_id=item_id,
        response_text=args.get("response_text") or args.get("answer"),
        response_structured=args.get("response_structured"),
    )
    attempt = await session.get(
        __import__("wax.db.models", fromlist=["AssessmentAttempt"]).AssessmentAttempt,
        attempt_id,
    )
    nxt = await svc.next_item(attempt.assessment_id, attempt_id) if attempt else None
    done = nxt is None
    if done and attempt:
        await svc.complete_attempt(attempt_id)
    return {
        "ok": True,
        "is_correct": resp.is_correct,
        "score": resp.score,
        "feedback": resp.feedback,
        "completed": done,
        "next_item": (
            {
                "item_id": str(nxt.id),
                "prompt": nxt.prompt,
                "item_type": nxt.item_type,
                "options": nxt.options,
            }
            if nxt
            else None
        ),
    }


HANDLERS["create_assessment"] = handle_create_assessment
HANDLERS["submit_assessment_answer"] = handle_submit_assessment_answer


async def handle_why_we_believe(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    from wax.memory.evidence import EvidenceService
    principal_id = ctx.get("principal_id")
    claim_key = args.get("claim_key")
    if not principal_id or not claim_key:
        return {"ok": False, "error": "claim_key_required"}
    return await EvidenceService(session).why_we_believe(principal_id, claim_key)


async def handle_record_evidence(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Record objective evidence — never invent a score as evidence."""
    from wax.memory.evidence import EvidenceService
    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    ev = await EvidenceService(session).record(
        principal_id=principal_id,
        evidence_type=args.get("evidence_type") or "performance",
        description=args.get("description") or "",
        claim_key=args.get("claim_key"),
        payload=args.get("payload") or {},
        assistance_level=args.get("assistance_level") or "unknown",
        weight=float(args.get("weight") or 0.5),
        source=args.get("source") or "observed",
        work_id=ctx.get("work_id"),
    )
    return {"ok": True, "evidence_id": str(ev.id), "claim_key": ev.claim_key}


async def handle_form_hypothesis(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    from wax.memory.evidence import EvidenceService
    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    hyp = await EvidenceService(session).form_hypothesis(
        principal_id=principal_id,
        claim_key=args.get("claim_key") or "claim:general",
        claim=args.get("claim") or "",
        initial_confidence=float(args.get("confidence") or 0.3),
        rationale=args.get("rationale"),
    )
    return {
        "ok": True,
        "hypothesis_id": str(hyp.id),
        "status": hyp.status,
        "confidence": hyp.confidence,
        "note": "Hypothesis is candidate/active — not stored as fact until confirmed by evidence",
    }


HANDLERS["why_we_believe"] = handle_why_we_believe
HANDLERS["record_evidence"] = handle_record_evidence
HANDLERS["form_hypothesis"] = handle_form_hypothesis

# Complete registry (must be after all handle_* definitions)


async def handle_propose_learning_check(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    from wax.memory.research_loop import ResearchLoopService
    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    prop = await ResearchLoopService(session).propose_next_test(principal_id)
    return {"ok": True, "proposal": prop}


async def handle_schedule_hypothesis_recheck(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    from wax.memory.research_loop import ResearchLoopService
    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    action = await ResearchLoopService(session).schedule_hypothesis_recheck(
        principal_id=principal_id,
        hypothesis_id=args.get("hypothesis_id"),
        delay_hours=float(args.get("delay_hours") or 24),
        message_hint=args.get("message_hint"),
    )
    if not action:
        return {"ok": False, "error": "could_not_schedule"}
    return {"ok": True, "scheduled_action_id": str(action.id), "execute_at": action.execute_at.isoformat() if getattr(action, "execute_at", None) else None}


async def handle_get_current_time(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Authoritative clock for the agent — never invent dates from the model."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    now = datetime.now(timezone.utc)
    tz_name = (args.get("timezone") or ctx.get("timezone") or "UTC").strip() or "UTC"
    local_iso = None
    try:
        local = now.astimezone(ZoneInfo(tz_name))
        local_iso = local.isoformat()
        dow = local.strftime("%A")
        date_s = local.strftime("%Y-%m-%d")
    except Exception:
        tz_name = "UTC"
        local_iso = now.isoformat()
        dow = now.strftime("%A")
        date_s = now.strftime("%Y-%m-%d")
    return {
        "ok": True,
        "utc": now.isoformat(),
        "timezone": tz_name,
        "local": local_iso,
        "date": date_s,
        "day_of_week": dow,
    }



async def handle_get_learner_state(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Safe domain read of current learner situation (tenant-scoped)."""
    from wax.domain.learner_state import build_learner_state_snapshot
    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    snap = await build_learner_state_snapshot(session, principal_id)
    return {"ok": True, "state": snap}


async def handle_schedule_at(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Schedule at absolute time. Prefer ISO UTC or local+timezone."""
    from datetime import datetime, timezone
    from wax.scheduler.service import SchedulerService

    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    when_s = args.get("execute_at") or args.get("when")
    if not when_s:
        return {"ok": False, "error": "execute_at_required"}
    try:
        when = datetime.fromisoformat(str(when_s).replace("Z", "+00:00"))
    except Exception:
        return {"ok": False, "error": "invalid_datetime"}
    tz_name = args.get("timezone")
    if when.tzinfo is None and not tz_name:
        when = when.replace(tzinfo=timezone.utc)
    elif when.tzinfo is None and tz_name:
        try:
            from zoneinfo import ZoneInfo
            when = when.replace(tzinfo=ZoneInfo(str(tz_name)))
        except Exception:
            when = when.replace(tzinfo=timezone.utc)
    action = await SchedulerService(session).schedule_at(
        principal_id=principal_id,
        action_type=str(args.get("action_type") or "tutor_followup"),
        execute_at=when,
        reason=args.get("reason"),
        payload={"message_hint": args.get("message_hint")},
        timezone_name=tz_name,
    )
    return {
        "ok": True,
        "scheduled_action_id": str(action.id),
        "execute_at": action.execute_at.isoformat() if action.execute_at else None,
    }


async def handle_pause_activity(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    from wax.work.activities import ActivityService
    from wax.db.models import Activity
    from sqlalchemy import select

    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    aid = args.get("activity_id")
    svc = ActivityService(session)
    if not aid:
        # pause most recent active
        stmt = (
            select(Activity)
            .where(Activity.principal_id == principal_id, Activity.status == "active")
            .order_by(Activity.updated_at.desc())
            .limit(1)
        )
        act = (await session.execute(stmt)).scalar_one_or_none()
        if not act:
            return {"ok": False, "error": "no_active_activity"}
        aid = act.id
    act = await svc.pause(aid, reason=args.get("reason"))
    if not act:
        return {"ok": False, "error": "not_found"}
    if act.principal_id != principal_id:
        return {"ok": False, "error": "forbidden"}
    return {"ok": True, "activity_id": str(act.id), "status": act.status}


async def handle_resume_activity(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    from wax.work.activities import ActivityService
    from wax.db.models import Activity
    from sqlalchemy import select

    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    aid = args.get("activity_id")
    svc = ActivityService(session)
    if not aid:
        stmt = (
            select(Activity)
            .where(Activity.principal_id == principal_id, Activity.status == "paused")
            .order_by(Activity.updated_at.desc())
            .limit(1)
        )
        act = (await session.execute(stmt)).scalar_one_or_none()
        if not act:
            return {"ok": False, "error": "no_paused_activity"}
        aid = act.id
    act = await svc.resume(aid)
    if not act:
        return {"ok": False, "error": "not_found"}
    if act.principal_id != principal_id:
        return {"ok": False, "error": "forbidden"}
    return {"ok": True, "activity_id": str(act.id), "status": act.status}


async def handle_set_preference(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    from wax.domain.preferences import update_preferences
    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    key = args.get("key")
    value = args.get("value")
    if not key:
        return {"ok": False, "error": "key_required"}
    allowed = {
        "language", "quiet_hours", "encouragement", "explanation_style",
        "message_length", "timezone", "proactivity_level",
        "emoji", "tone", "learning_style",
    }
    if key not in allowed:
        return {"ok": False, "error": "key_not_allowed", "allowed": sorted(allowed)}
    prefs = await update_preferences(session, principal_id, {key: value})
    return {"ok": True, "preferences": prefs}


async def handle_research_fetch(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    from wax.research.fetch import fetch_url
    url = (args.get("url") or "").strip()
    if not url:
        return {"ok": False, "error": "url_required"}
    return await fetch_url(url)


async def handle_research_search(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    from wax.research.fetch import search_web
    return await search_web(args.get("query") or "")


async def handle_record_assessment_timeout(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Mark current assessment item timed out and return next item if any."""
    from wax.assessment.service import AssessmentService
    attempt_id = args.get("attempt_id") or (ctx.get("input_payload") or {}).get("attempt_id")
    item_id = args.get("item_id")
    if not attempt_id:
        return {"ok": False, "error": "attempt_id_required"}
    return await AssessmentService(session).record_item_timeout(
        attempt_id=attempt_id, item_id=item_id
    )


async def handle_workspace_env(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Read or update per-learner workspace environment manifest."""
    from wax.terminal.env_manifest import load_manifest, record_package, has_package
    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    action = (args.get("action") or "get").lower()
    if action == "get":
        return {"ok": True, "manifest": load_manifest(principal_id)}
    if action == "has_package":
        name = args.get("name") or ""
        return {"ok": True, "name": name, "installed": has_package(principal_id, name)}
    if action == "record_package":
        name = args.get("name")
        if not name:
            return {"ok": False, "error": "name_required"}
        man = record_package(
            principal_id, name, version=args.get("version"), source=args.get("source") or "pip"
        )
        return {"ok": True, "manifest": man}
    return {"ok": False, "error": "unknown_action"}


async def handle_check_quiet_hours(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    from datetime import datetime, timezone
    from wax.domain.preferences import get_preferences, is_in_quiet_hours
    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    prefs = await get_preferences(session, principal_id)
    now = datetime.now(timezone.utc)
    # Use learner timezone if set
    tz_name = prefs.get("timezone")
    hhmm = now.strftime("%H:%M")
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            hhmm = now.astimezone(ZoneInfo(tz_name)).strftime("%H:%M")
        except Exception:
            pass
    quiet = is_in_quiet_hours(prefs, hhmm)
    return {"ok": True, "quiet_hours": quiet, "local_hhmm": hhmm, "timezone": tz_name}


async def handle_cancel_schedule(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    from wax.scheduler.service import SchedulerService
    from uuid import UUID
    principal_id = ctx.get("principal_id")
    aid = args.get("scheduled_action_id") or args.get("action_id")
    if not aid:
        return {"ok": False, "error": "action_id_required"}
    try:
        uid = UUID(str(aid))
    except Exception:
        return {"ok": False, "error": "invalid_id"}
    action = await SchedulerService(session).cancel(uid, principal_id=principal_id)
    if not action:
        return {"ok": False, "error": "not_found_or_forbidden"}
    return {"ok": True, "status": action.status, "action_id": str(action.id)}


async def handle_schedule_series(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    from datetime import datetime, timezone, timedelta
    from wax.scheduler.service import SchedulerService
    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    hours = args.get("interval_hours")
    count = args.get("count") or 3
    if hours is None:
        return {"ok": False, "error": "interval_hours_required"}
    first_s = args.get("first_at")
    if first_s:
        try:
            first_at = datetime.fromisoformat(str(first_s).replace("Z", "+00:00"))
        except Exception:
            return {"ok": False, "error": "invalid_first_at"}
    else:
        delay = float(args.get("delay_hours") or 1)
        first_at = datetime.now(timezone.utc) + timedelta(hours=delay)
    actions = await SchedulerService(session).schedule_series(
        principal_id=principal_id,
        action_type=str(args.get("action_type") or "tutor_followup"),
        first_at=first_at,
        interval_hours=float(hours),
        count=int(count),
        reason=args.get("reason"),
        payload={"message_hint": args.get("message_hint")},
    )
    return {
        "ok": True,
        "count": len(actions),
        "ids": [str(a.id) for a in actions],
    }


async def handle_redeliver_artifact(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Retry delivery of an existing artifact without regenerating content."""
    from uuid import UUID
    from wax.db.models import Artifact, Delivery
    from wax.artifacts.storage import read_bytes

    principal_id = ctx.get("principal_id")
    aid = args.get("artifact_id")
    if not aid or not principal_id:
        return {"ok": False, "error": "artifact_id_and_principal_required"}
    try:
        art = await session.get(Artifact, UUID(str(aid)))
    except Exception:
        return {"ok": False, "error": "invalid_id"}
    if not art or art.principal_id != principal_id:
        return {"ok": False, "error": "not_found"}
    channel = (args.get("channel") or ctx.get("channel") or "telegram").lower()
    target = args.get("target_external_id") or ctx.get("target_external_id")
    if not target:
        return {"ok": False, "error": "target_required"}
    delivery = Delivery(
        id=__import__("uuid").uuid4(),
        work_id=ctx.get("work_id"),
        principal_id=principal_id,
        channel=channel,
        target_external_id=str(target),
        content=f"[artifact:{art.id}]",
        status="pending",
        idempotency_key=f"artifact-redeliver:{art.id}:{channel}:{__import__('uuid').uuid4().hex[:8]}",
        metadata_={"artifact_id": str(art.id), "uri": art.uri},
    )
    session.add(delivery)
    await session.flush()
    return {
        "ok": True,
        "delivery_id": str(delivery.id),
        "artifact_id": str(art.id),
        "note": "Queued redelivery of existing artifact bytes — content not regenerated.",
    }


async def handle_export_learner_data(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    from wax.domain.export import export_principal_package
    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    limit = int(args.get("message_limit") or 200)
    return await export_principal_package(session, principal_id, message_limit=limit)


async def handle_link_channel_identity(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    from wax.domain.identity import link_identity_to_principal
    principal_id = ctx.get("principal_id")
    channel = args.get("channel")
    external_id = args.get("external_id")
    if not principal_id or not channel or not external_id:
        return {"ok": False, "error": "channel_and_external_id_required"}
    identity = await link_identity_to_principal(
        session,
        principal_id=principal_id,
        channel=str(channel),
        external_id=str(external_id),
        display_name=args.get("display_name"),
        make_primary=bool(args.get("make_primary")),
    )
    return {
        "ok": True,
        "identity_id": str(identity.id),
        "channel": identity.channel,
        "external_id": identity.external_id,
        "is_primary": identity.is_primary,
    }


async def handle_create_html_page(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Create a branded HTML artifact (ephemeral study page)."""
    from uuid import uuid4
    from wax.artifacts.html_page import render_branded_html
    from wax.artifacts.storage import get_storage
    from wax.db.models import Artifact
    from wax.config import get_settings

    if not getattr(get_settings(), "allow_html_artifacts", True):
        return {"ok": False, "error": "html_artifacts_disabled"}
    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    title = args.get("title") or "Notes"
    content = args.get("content") or args.get("body") or ""
    html = render_branded_html(title=title, body=content)
    from wax.artifacts.storage import store_bytes
    uri = store_bytes(
        principal_id,
        f"{uuid4().hex[:8]}-page.html",
        html.encode("utf-8"),
        content_type="text/html; charset=utf-8",
    )
    art = Artifact(
        id=uuid4(),
        principal_id=principal_id,
        kind="html_page",
        title=title[:500],
        content_type="text/html",
        uri=uri,
        structured={"format": "html", "ephemeral": True},
        metadata_={"source": "create_html_page"},
    )
    session.add(art)
    await session.flush()
    from wax.artifacts.access import make_download_token, public_page_url, public_download_url
    token = make_download_token(str(art.id), str(principal_id), ttl_seconds=86400 * 7)
    page_url = public_page_url(str(art.id), token)
    dl_url = public_download_url(str(art.id), token)
    return {
        "ok": True,
        "artifact_id": str(art.id),
        "uri": uri,
        "title": title,
        "page_url": page_url,
        "download_url": dl_url,
        "note": (
            "Share page_url with the learner so they can open the mini page in a browser. "
            "Requires PUBLIC_BASE_URL (your Railway web URL). Link expires in 7 days."
            if page_url
            else "Set PUBLIC_BASE_URL to your Railway web URL (e.g. https://web-xxx.up.railway.app) to get openable page links."
        ),
    }


async def handle_request_channel_link(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Start OTP (or knowledge) link of another messaging channel to this learner."""
    from wax.domain.channel_link import request_otp_link, request_knowledge_link
    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    target_channel = (args.get("target_channel") or args.get("channel") or "").lower()
    target_id = args.get("target_external_id") or args.get("phone") or args.get("chat_id") or args.get("number")
    method = (args.get("method") or "otp").lower()
    source = (ctx.get("channel") or args.get("source_channel") or "telegram").lower()
    if method == "knowledge":
        questions = args.get("questions") or []
        if isinstance(questions, str):
            questions = []
        return await request_knowledge_link(
            session,
            principal_id=principal_id,
            source_channel=source,
            target_channel=target_channel,
            target_external_id=str(target_id or ""),
            questions=list(questions),
        )
    return await request_otp_link(
        session,
        principal_id=principal_id,
        source_channel=source,
        target_channel=target_channel,
        target_external_id=str(target_id or ""),
    )


async def handle_confirm_channel_link(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Confirm OTP code pasted by the learner, or knowledge answers."""
    from wax.domain.channel_link import confirm_otp_link, confirm_knowledge_link
    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    code = args.get("code") or args.get("otp") or args.get("verification_code")
    if code:
        return await confirm_otp_link(
            session,
            principal_id=principal_id,
            code=str(code),
            challenge_id=args.get("challenge_id"),
        )
    if args.get("challenge_id") and args.get("answers"):
        return await confirm_knowledge_link(
            session,
            principal_id=principal_id,
            challenge_id=str(args["challenge_id"]),
            answers=list(args.get("answers") or []),
        )
    return {"ok": False, "error": "code_or_answers_required"}


async def handle_transcribe_audio(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Transcribe a local audio/voice file. Does not invent success."""
    from wax.tools.transcription import transcribe_local_audio
    from wax.terminal.workspace import principal_workspace

    path = (args.get("path") or "").strip()
    principal_id = ctx.get("principal_id")
    if not path and ctx.get("local_media_path"):
        path = str(ctx.get("local_media_path"))
    if not path:
        return {"ok": False, "error": "path_required"}
    # Path isolation: must live under principal workspace when principal known
    if principal_id:
        base = principal_workspace(principal_id)
        try:
            resolved = Path(path).resolve()
            if not str(resolved).startswith(str(base.resolve())):
                return {"ok": False, "error": "path_escape"}
        except Exception:
            return {"ok": False, "error": "invalid_path"}
    return await transcribe_local_audio(
        path,
        language=args.get("language"),
        work_id=str(ctx.get("work_id") or "") or None,
        principal_id=str(ctx.get("principal_id") or "") or None,
    )


async def handle_ingest_document(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """
    Ingest learner-provided material into principal-scoped knowledge.
    Accepts raw text and/or a workspace path (OCR/pdf text extracted when useful).
    """
    from wax.knowledge.ingest import KnowledgeIngestService
    from wax.terminal.workspace import principal_workspace
    from wax.terminal.executor import get_terminal

    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    title = (args.get("title") or "Learner material").strip()[:500]
    text = (args.get("text") or "").strip()
    path = (args.get("path") or "").strip()
    kind = (args.get("kind") or "document").strip()[:80]

    if path and not text:
        base = principal_workspace(principal_id)
        try:
            resolved = Path(path).resolve()
            if not str(resolved).startswith(str(base.resolve())):
                return {"ok": False, "error": "path_escape"}
        except Exception:
            return {"ok": False, "error": "invalid_path"}
        if not resolved.is_file():
            return {"ok": False, "error": "file_not_found"}
        # Prefer plain text read for .txt/.md; otherwise terminal inspect
        if resolved.suffix.lower() in {".txt", ".md", ".csv", ".json"}:
            try:
                text = resolved.read_text(encoding="utf-8", errors="replace")[:200000]
            except Exception as e:
                return {"ok": False, "error": f"read_failed:{e}"}
        else:
            term = get_terminal()
            insp = await term.inspect_media_file(str(resolved), principal_id=principal_id)
            if not insp.success:
                return {"ok": False, "error": insp.error or "inspect_failed"}
            text = insp.stdout or ""
            # Prefer OCR/pdftotext sections if present
            if "ocr:" in text.lower() or "pdftotext:" in text.lower():
                pass
            if not text.strip():
                return {"ok": False, "error": "no_text_extracted"}

    if not text.strip():
        return {"ok": False, "error": "text_or_path_required"}

    svc = KnowledgeIngestService(session)
    source = await svc.ingest_text(
        principal_id=principal_id,
        title=title,
        text=text,
        kind=kind,
    )
    return {
        "ok": True,
        "knowledge_source_id": str(source.id),
        "title": source.title,
        "status": source.status,
        "kind": source.kind,
        "chars": len(text),
    }




async def handle_extract_video_audio(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Extract audio track from a video into workspace (ffmpeg). Does not transcribe."""
    from pathlib import Path as P
    import asyncio
    import shutil
    from wax.terminal.workspace import principal_workspace

    path = (args.get("path") or ctx.get("local_media_path") or "").strip()
    principal_id = ctx.get("principal_id")
    if not path:
        return {"ok": False, "error": "path_required"}
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    base = principal_workspace(principal_id)
    try:
        src = P(path).resolve()
        if not str(src).startswith(str(base.resolve())):
            return {"ok": False, "error": "path_escape"}
    except Exception:
        return {"ok": False, "error": "invalid_path"}
    if not src.is_file():
        return {"ok": False, "error": "file_not_found"}
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {"ok": False, "error": "ffmpeg_not_found"}
    out = base / "tmp" / f"{src.stem}-audio.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out),
    ]
    from wax.world.run_tool import run_in_world

    result = await run_in_world(
        str(principal_id), argv, cwd_rel="workspace", network_mode="none", timeout_sec=120.0
    )
    if (result.error or "").startswith("Timed out"):
        return {"ok": False, "error": "ffmpeg_timeout"}
    if not result.success or not out.is_file():
        return {
            "ok": False,
            "error": "extract_audio_failed",
            "detail": (result.stderr or result.error or "")[:300],
            "backend": result.backend,
        }
    return {
        "ok": True,
        "path": str(out),
        "kind": "audio",
        "note": "Audio extracted. Call transcribe_audio on this path if you need a transcript.",
        "capabilities": ["transcribe", "inspect"],
        "backend": result.backend,
    }


async def handle_extract_video_frames(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Extract a limited number of frames from video (ffmpeg). Cap enforced."""
    from pathlib import Path as P
    import asyncio
    import shutil
    from wax.terminal.workspace import principal_workspace

    path = (args.get("path") or ctx.get("local_media_path") or "").strip()
    principal_id = ctx.get("principal_id")
    if not path:
        return {"ok": False, "error": "path_required"}
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    base = principal_workspace(principal_id)
    try:
        src = P(path).resolve()
        if not str(src).startswith(str(base.resolve())):
            return {"ok": False, "error": "path_escape"}
    except Exception:
        return {"ok": False, "error": "invalid_path"}
    if not src.is_file():
        return {"ok": False, "error": "file_not_found"}
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {"ok": False, "error": "ffmpeg_not_found"}
    max_frames = min(int(args.get("max_frames") or 3), 8)
    fps = float(args.get("fps") or 0.2)  # ~1 frame / 5s default sampling intent
    out_dir = base / "tmp" / f"{src.stem}-frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(out_dir / "frame-%03d.jpg")
    # fps filter with frame limit via -frames:v
    argv = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src),
        "-vf", f"fps={fps}",
        "-frames:v", str(max_frames),
        pattern,
    ]
    from wax.world.run_tool import run_in_world

    result = await run_in_world(
        str(principal_id), argv, cwd_rel="workspace", network_mode="none", timeout_sec=120.0
    )
    if (result.error or "").startswith("Timed out"):
        return {"ok": False, "error": "ffmpeg_timeout"}
    frames = sorted(str(f) for f in out_dir.glob("frame-*.jpg"))
    if not frames:
        return {
            "ok": False,
            "error": "no_frames",
            "detail": (result.stderr or result.error or "")[:300],
            "backend": result.backend,
        }
    return {
        "ok": True,
        "frames": frames[:max_frames],
        "count": len(frames[:max_frames]),
        "note": "Frames extracted. Call inspect_media or describe_image on a frame path if needed.",
        "capabilities": ["ocr", "vision", "inspect"],
    }



async def handle_extract_subtitles(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Extract subtitle stream from video when present (ffmpeg)."""
    from pathlib import Path as P
    import asyncio
    import shutil
    from wax.terminal.workspace import principal_workspace
    from wax.media.probe import probe_local_file

    path = (args.get("path") or ctx.get("local_media_path") or "").strip()
    principal_id = ctx.get("principal_id")
    if not path:
        return {"ok": False, "error": "path_required"}
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    base = principal_workspace(principal_id)
    try:
        src = P(path).resolve()
        if not str(src).startswith(str(base.resolve())):
            return {"ok": False, "error": "path_escape"}
    except Exception:
        return {"ok": False, "error": "invalid_path"}
    if not src.is_file():
        return {"ok": False, "error": "file_not_found"}
    probe = probe_local_file(src)
    if probe.has_subtitle_stream is False:
        return {
            "ok": False,
            "error": "no_subtitle_stream",
            "probe": {"kind": probe.kind, "has_subtitle_stream": probe.has_subtitle_stream},
        }
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {"ok": False, "error": "ffmpeg_not_found"}
    out = base / "tmp" / f"{src.stem}-subs.srt"
    out.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src), "-map", "0:s:0", str(out),
    ]
    from wax.world.run_tool import run_in_world

    result = await run_in_world(
        str(principal_id), argv, cwd_rel="workspace", network_mode="none", timeout_sec=90.0
    )
    if (result.error or "").startswith("Timed out"):
        return {"ok": False, "error": "ffmpeg_timeout"}
    if not result.success or not out.is_file() or out.stat().st_size == 0:
        return {
            "ok": False,
            "error": "subtitle_extract_failed",
            "detail": (result.stderr or result.error or "")[:300],
            "backend": result.backend,
        }
    text_out = out.read_text(encoding="utf-8", errors="replace")[:20000]
    return {
        "ok": True,
        "path": str(out),
        "text": text_out,
        "kind": "subtitle",
        "chars": len(text_out),
    }




async def handle_world_jobs(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """List or inspect World execution records (compatibility + recovery view)."""
    from wax.world.manager import get_or_create_world
    from wax.world.discover import discover

    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    world = get_or_create_world(str(principal_id))
    snap = discover(world, sections=["jobs", "lifecycle"])
    return {"ok": True, "jobs": snap.get("jobs") or [], "lifecycle": snap.get("lifecycle")}

async def handle_world_discover(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Discover observed state of the learner's personal World."""
    from wax.world.manager import get_or_create_world
    from wax.world.discover import discover
    from wax.world.errors import WorldError

    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    try:
        world = get_or_create_world(str(principal_id))
        try:
            from wax.world.persist import upsert_world

            await upsert_world(session, world)
        except Exception:
            pass
        sections = args.get("sections")
        if isinstance(sections, str):
            sections = [s.strip() for s in sections.split(",") if s.strip()]
        return discover(world, sections=sections)
    except WorldError as e:
        return e.to_dict()
    except Exception as e:
        return {"ok": False, "error": "world_discover_failed", "detail": str(e)[:300]}


async def handle_world_exec(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Execute argv or a script inside the learner World (isolated). No command allowlist."""
    from wax.world.manager import get_or_create_world
    from wax.world.exec import world_exec
    from wax.world.errors import WorldError

    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    argv = args.get("argv")
    if isinstance(argv, str):
        import shlex
        argv = shlex.split(argv)
    try:
        world = get_or_create_world(str(principal_id))
        return await world_exec(
            world,
            argv=argv,
            script=args.get("script"),
            runtime=args.get("runtime") or "python",
            cwd_rel=args.get("cwd") or "workspace",
            network_mode=args.get("network_mode") or "none",
            budget_class=args.get("budget_class") or "interactive",
        )
    except WorldError as e:
        return e.to_dict()
    except Exception as e:
        return {"ok": False, "error": "world_exec_failed", "detail": str(e)[:300]}


async def handle_world_acquire(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Acquire software into the World (v1: python_package via pip into world venv)."""
    from wax.world.manager import get_or_create_world
    from wax.world.acquire import acquire
    from wax.world.providers.base import AcquireRequest
    from wax.world.errors import WorldError

    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    name = (args.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "name_required"}
    kind = (args.get("kind") or "python_package").strip()
    try:
        world = get_or_create_world(str(principal_id))
        req = AcquireRequest(
            kind=kind,
            name=name,
            version_spec=args.get("version_spec") or args.get("version"),
        )
        return await acquire(world, req)
    except WorldError as e:
        return e.to_dict()
    except Exception as e:
        return {"ok": False, "error": "world_acquire_failed", "detail": str(e)[:300]}


async def handle_world_files(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """List/read/write files inside the learner World."""
    from wax.world.manager import get_or_create_world
    from wax.world import files as wfiles
    from wax.world.errors import WorldError

    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    action = (args.get("action") or "list").strip()
    path = args.get("path") or "workspace"
    try:
        world = get_or_create_world(str(principal_id))
        if action == "list":
            return wfiles.list_files(world, path)
        if action == "read":
            return wfiles.read_file(world, path)
        if action == "write":
            return wfiles.write_file(world, path, args.get("content") or "")
        if action == "mkdir":
            return wfiles.mkdir(world, path)
        if action == "delete":
            return wfiles.delete_file(world, path)
        return {"ok": False, "error": "unknown_action"}
    except WorldError as e:
        return e.to_dict()
    except Exception as e:
        return {"ok": False, "error": "world_files_failed", "detail": str(e)[:300]}


# Final registry — must run AFTER every handle_* is defined
HANDLERS.update({
    "schedule_followup": handle_schedule_followup,
    "create_artifact": handle_create_artifact,
    "run_python": handle_run_python,
    "present_choices": handle_present_choices,
    "get_current_time": handle_get_current_time,
    "get_learner_state": handle_get_learner_state,
    "schedule_at": handle_schedule_at,
    "pause_activity": handle_pause_activity,
    "resume_activity": handle_resume_activity,
    "set_preference": handle_set_preference,
    "research_fetch": handle_research_fetch,
    "research_search": handle_research_search,
    "record_assessment_timeout": handle_record_assessment_timeout,
    "workspace_env": handle_workspace_env,
    "check_quiet_hours": handle_check_quiet_hours,
    "cancel_schedule": handle_cancel_schedule,
    "schedule_series": handle_schedule_series,
    "redeliver_artifact": handle_redeliver_artifact,
    "export_learner_data": handle_export_learner_data,
    "link_channel_identity": handle_link_channel_identity,
    "request_channel_link": handle_request_channel_link,
    "confirm_channel_link": handle_confirm_channel_link,
    "create_html_page": handle_create_html_page,
    "inspect_memories": handle_inspect_memories,
    "manage_goal": handle_manage_goal,
    "forget_memory": handle_forget_memory,
    "fetch_inbound_media": handle_fetch_inbound_media,
    "list_workspace": handle_list_workspace,
    "inspect_media": handle_inspect_media,
    "workspace_command": handle_workspace_command,
    "describe_image": handle_describe_image,
    "list_artifacts": handle_list_artifacts,
    "read_artifact": handle_read_artifact,
    "write_workspace_file": handle_write_workspace_file,
    "read_workspace_file": handle_read_workspace_file,
    "schedule_continuous": handle_schedule_continuous,
    "start_activity": handle_start_activity,
    "complete_activity": handle_complete_activity,
    "update_concept_state": handle_update_concept_state,
    "create_assessment": handle_create_assessment,
    "submit_assessment_answer": handle_submit_assessment_answer,
    "why_we_believe": handle_why_we_believe,
    "record_evidence": handle_record_evidence,
    "form_hypothesis": handle_form_hypothesis,
    "propose_learning_check": handle_propose_learning_check,
    "schedule_hypothesis_recheck": handle_schedule_hypothesis_recheck,
    "transcribe_audio": handle_transcribe_audio,
    "ingest_document": handle_ingest_document,
    "extract_video_audio": handle_extract_video_audio,
    "extract_video_frames": handle_extract_video_frames,
    "extract_subtitles": handle_extract_subtitles,
    "world_discover": handle_world_discover,
    "world_exec": handle_world_exec,
    "world_acquire": handle_world_acquire,
    "world_files": handle_world_files,
    "world_jobs": handle_world_jobs,
})
