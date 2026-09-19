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
HANDLERS.update({
    "schedule_followup": handle_schedule_followup,
    "create_artifact": handle_create_artifact,
    "run_python": handle_run_python,
    "present_choices": handle_present_choices,
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
})


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


HANDLERS["propose_learning_check"] = handle_propose_learning_check
HANDLERS["schedule_hypothesis_recheck"] = handle_schedule_hypothesis_recheck
