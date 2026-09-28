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


async def handle_schedule(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """
    Generic durable scheduling primitive.

    Accepts absolute ISO execute_at, delay_hours, or natural when_text.
    Guarantees persistence, restart survival, and worker execution at the requested time.
    Infrastructure does not enforce semantic meaning — payload is flexible.
    """
    from datetime import datetime, timedelta, timezone
    from wax.learner.temporal import TemporalService, resolve_natural_time, now_in_tz
    from wax.learner.model import build_learner_model

    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}

    reason = str(args.get("reason") or args.get("target") or "scheduled_wake").strip()
    tz = str(args.get("timezone") or ctx.get("timezone") or "UTC").strip()
    if principal_id:
        try:
            model = await build_learner_model(session, principal_id, include_retention=False, include_events=False)
            tz = model.timezone or tz
        except Exception:
            pass

    execute_at = None
    if args.get("execute_at"):
        try:
            execute_at = datetime.fromisoformat(str(args["execute_at"]).replace("Z", "+00:00"))
            if execute_at.tzinfo is None:
                execute_at = execute_at.replace(tzinfo=timezone.utc)
        except Exception:
            return {"ok": False, "error": "invalid_execute_at_format"}
    elif args.get("delay_hours") is not None:
        try:
            dh = float(args["delay_hours"])
            execute_at = datetime.now(timezone.utc) + timedelta(hours=max(0.01, dh))
        except (TypeError, ValueError):
            return {"ok": False, "error": "invalid_delay_hours"}
    elif args.get("when_text"):
        resolved = resolve_natural_time(str(args["when_text"]), reference=now_in_tz(tz), tz_name=tz)
        if not resolved:
            return {"ok": False, "error": "could_not_resolve_time", "timezone": tz}
        execute_at = resolved["execute_at"]

    if not execute_at:
        return {"ok": False, "error": "execute_at_delay_hours_or_when_text_required"}

    payload = args.get("payload") if isinstance(args.get("payload"), dict) else {}
    if args.get("message_hint"):
        payload["message_hint"] = args.get("message_hint")

    intent = await TemporalService(session).create_intent(
        principal_id=principal_id,
        purpose=str(args.get("purpose") or "scheduled_wake"),
        target=reason,
        execute_at=execute_at,
        timezone_name=tz,
        original_request=reason,
        flexibility="soft",
        payload=payload,
    )
    return {
        "ok": True,
        "action_id": str(intent.scheduled_action_id) if intent.scheduled_action_id else str(intent.id),
        "intent_id": str(intent.id),
        "execute_at": intent.execute_at.isoformat(),
        "reason": reason,
        "timezone": intent.timezone,
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
        "create_surface",
        "update_surface",
        "revoke_surface",
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
    """Legacy adapter → multiple TemporalIntents."""
    from datetime import datetime, timedelta, timezone
    from wax.learner.temporal import TemporalService
    from wax.learner.model import build_learner_model

    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    hours_list = args.get("hours_from_now") or args.get("delays_hours") or [24]
    if isinstance(hours_list, (int, float)):
        hours_list = [hours_list]
    reason = args.get("reason") or "ongoing follow-up"
    hint = args.get("message_hint") or reason
    tz = "UTC"
    try:
        model = await build_learner_model(session, principal_id, include_retention=False, include_events=False)
        tz = model.timezone or tz
    except Exception:
        pass
    svc = TemporalService(session)
    created = []
    now = datetime.now(timezone.utc)
    for h in list(hours_list)[:12]:
        try:
            hours = float(h)
        except (TypeError, ValueError):
            continue
        when = now + timedelta(hours=max(0.05, hours))
        intent = await svc.create_intent(
            principal_id=principal_id,
            purpose="followup",
            target=reason,
            execute_at=when,
            timezone_name=tz,
            original_request=reason,
            flexibility="soft",
            payload={"message_hint": hint, "legacy_schedule_continuous": True, "series": True},
        )
        created.append({"intent_id": str(intent.id), "hours": hours, "at": intent.execute_at.isoformat()})
    return {"ok": True, "scheduled": created, "count": len(created), "via": "temporal_intent"}





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



async def handle_resolve_natural_time(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Resolve natural language time using authoritative clock + learner timezone."""
    from wax.learner.temporal import resolve_natural_time, now_in_tz
    from wax.learner.model import build_learner_model

    principal_id = ctx.get("principal_id")
    text = args.get("text") or args.get("when") or ""
    tz = args.get("timezone") or "UTC"
    if principal_id:
        try:
            model = await build_learner_model(session, principal_id, include_retention=False, include_events=False)
            tz = model.timezone or tz
        except Exception:
            pass
    ref = now_in_tz(tz)
    resolved = resolve_natural_time(text, reference=ref, tz_name=tz)
    if not resolved:
        return {"ok": False, "error": "unresolved", "timezone": tz, "now_local": ref.isoformat()}
    return {"ok": True, **{k: v for k, v in resolved.items() if k != "execute_at"}, "execute_at": resolved["execute_at"].isoformat()}


async def handle_schedule_intent(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Schedule a temporal *intent* (reminder/review/followup) — not fixed wording."""
    from wax.learner.temporal import TemporalService, resolve_natural_time, now_in_tz
    from wax.learner.model import build_learner_model
    from datetime import datetime, timezone

    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    target = (args.get("target") or args.get("reason") or "").strip()
    if not target:
        return {"ok": False, "error": "target_required"}
    purpose = (args.get("purpose") or "reminder").strip()
    tz = args.get("timezone") or "UTC"
    try:
        model = await build_learner_model(session, principal_id, include_retention=False, include_events=False)
        tz = model.timezone or tz
    except Exception:
        pass
    when = args.get("execute_at")
    if not when and args.get("when_text"):
        resolved = resolve_natural_time(str(args["when_text"]), reference=now_in_tz(tz), tz_name=tz)
        if not resolved:
            return {"ok": False, "error": "could_not_resolve_time", "timezone": tz}
        execute_at = resolved["execute_at"]
        flexibility = resolved.get("flexibility") or "hard"
    else:
        if not when:
            return {"ok": False, "error": "execute_at_or_when_text_required"}
        execute_at = datetime.fromisoformat(str(when).replace("Z", "+00:00"))
        if execute_at.tzinfo is None:
            execute_at = execute_at.replace(tzinfo=timezone.utc)
        flexibility = args.get("flexibility") or "hard"
    intent = await TemporalService(session).create_intent(
        principal_id=principal_id,
        purpose=purpose,
        target=target,
        execute_at=execute_at,
        timezone_name=tz,
        original_request=args.get("original_request") or target,
        flexibility=flexibility,
        completion_condition=args.get("completion_condition"),
        concept_key=args.get("concept_key"),
    )
    return {
        "ok": True,
        "intent_id": str(intent.id),
        "execute_at": intent.execute_at.isoformat(),
        "timezone": intent.timezone,
        "status": intent.status,
        "purpose": intent.purpose,
    }


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
    from wax.learner.temporal import TemporalService
    from wax.learner.model import build_learner_model

    purpose = str(args.get("action_type") or "tutor_followup")
    if purpose in ("tutor_followup", "reminder", "review"):
        purpose_map = {"tutor_followup": "followup", "reminder": "reminder", "review": "review"}
        purpose = purpose_map.get(purpose, "followup")
    tz = tz_name or "UTC"
    try:
        model = await build_learner_model(session, principal_id, include_retention=False, include_events=False)
        tz = model.timezone or tz
    except Exception:
        pass
    intent = await TemporalService(session).create_intent(
        principal_id=principal_id,
        purpose=purpose if purpose in ("reminder", "review", "followup", "deadline") else "followup",
        target=str(args.get("reason") or args.get("message_hint") or "follow-up"),
        execute_at=when,
        timezone_name=tz,
        original_request=str(args.get("reason") or ""),
        flexibility=str(args.get("flexibility") or "hard"),
        payload={"message_hint": args.get("message_hint"), "legacy_schedule_at": True},
    )
    return {
        "ok": True,
        "scheduled_action_id": str(intent.scheduled_action_id) if intent.scheduled_action_id else None,
        "intent_id": str(intent.id),
        "execute_at": intent.execute_at.isoformat() if intent.execute_at else None,
        "via": "temporal_intent",
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
        uid = str(aid)
    action = await SchedulerService(session).cancel(uid, principal_id=principal_id)
    if not action:
        return {"ok": False, "error": "not_found_or_forbidden"}
    return {"ok": True, "status": action.status, "action_id": str(action.id)}


async def handle_schedule_series(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Legacy series adapter → one TemporalIntent per occurrence."""
    from datetime import datetime, timezone, timedelta
    from wax.learner.temporal import TemporalService
    from wax.learner.model import build_learner_model

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
    if first_at.tzinfo is None:
        first_at = first_at.replace(tzinfo=timezone.utc)
    tz = "UTC"
    try:
        model = await build_learner_model(session, principal_id, include_retention=False, include_events=False)
        tz = model.timezone or tz
    except Exception:
        pass
    purpose_raw = str(args.get("action_type") or "followup")
    purpose = "review" if "review" in purpose_raw else "followup"
    reason = str(args.get("reason") or "series")
    svc = TemporalService(session)
    created = []
    for i in range(max(1, min(int(count), 12))):
        when = first_at + timedelta(hours=float(hours) * i)
        intent = await svc.create_intent(
            principal_id=principal_id,
            purpose=purpose,
            target=reason,
            execute_at=when,
            timezone_name=tz,
            original_request=reason,
            flexibility="soft",
            payload={"message_hint": args.get("message_hint"), "legacy_schedule_series": True, "index": i},
        )
        created.append({"intent_id": str(intent.id), "at": intent.execute_at.isoformat()})
    return {"ok": True, "count": len(created), "series": created, "via": "temporal_intent"}


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
    from wax.domain.identity import IdentityConflictError, link_identity_to_principal
    principal_id = ctx.get("principal_id")
    channel = args.get("channel")
    external_id = args.get("external_id")
    if not principal_id or not channel or not external_id:
        return {"ok": False, "error": "channel_and_external_id_required"}
    try:
        identity = await link_identity_to_principal(
            session,
            principal_id=principal_id,
            channel=str(channel),
            external_id=str(external_id),
            display_name=args.get("display_name"),
            make_primary=bool(args.get("make_primary")),
            allow_reassign=False,
        )
    except IdentityConflictError:
        return {
            "ok": False,
            "error": "identity_conflict",
            "note": "That channel identity already belongs to another learner.",
        }
    return {
        "ok": True,
        "identity_id": str(identity.id),
        "channel": identity.channel,
        "external_id": identity.external_id,
        "is_primary": identity.is_primary,
    }





async def handle_retain_surface(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Mark a surface as important so infrastructure extends retention within policy."""
    from wax.surfaces.service import SurfaceService
    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    sid = args.get("surface_id") or ctx.get("surface_id")
    if not sid:
        return {"ok": False, "error": "surface_id_required"}
    keep = args.get("keep", True)
    if isinstance(keep, str):
        keep = keep.lower() not in ("0", "false", "no")
    return await SurfaceService(session).set_retention(sid, principal_id, keep=bool(keep))


async def handle_inspect_surface(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    from wax.surfaces.service import SurfaceService
    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    sid = args.get("surface_id") or ctx.get("surface_id")
    if not sid:
        return {"ok": False, "error": "surface_id_required"}
    return await SurfaceService(session).inspect(sid, principal_id)


async def handle_create_surface(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """
    Create a temporary AI-authored web surface (HTML/CSS/JS experience).

    The AI authors the experience. WAX hosts and isolates it.
    Prefer updating an existing surface over creating a new one for ordinary edits.
    """
    from wax.surfaces.service import SurfaceService

    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    html = args.get("html") or args.get("content") or ""
    if not str(html).strip():
        return {"ok": False, "error": "html_required"}
    idem = args.get("idempotency_key") or ctx.get("tool_call_id") or ctx.get("work_id")
    svc = SurfaceService(session)
    try:
        return await svc.create(
            principal_id=principal_id,
            html=str(html),
            title=args.get("title"),
            description=args.get("description"),
            work_id=ctx.get("work_id") or args.get("work_id"),
            parent_surface_id=args.get("parent_surface_id"),
            preferred_lifetime_hours=args.get("preferred_lifetime_hours"),
            lifecycle_intent=args.get("lifecycle_intent"),
            requested_scopes=args.get("scopes"),
            idempotency_key=str(idem) if idem else None,
            source_note=args.get("source_note"),
            initial_state=args.get("initial_state") if isinstance(args.get("initial_state"), dict) else None,
        )
    except Exception as e:
        logger.error("create_surface_failed", error=str(e))
        return {"ok": False, "error": "surface_create_failed", "detail": str(e)[:200]}


async def handle_update_surface(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Update an existing surface in place (same URL). Rename, change HTML, merge state."""
    from wax.surfaces.service import SurfaceService

    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    surface_id = args.get("surface_id") or ctx.get("surface_id")
    if not surface_id:
        return {"ok": False, "error": "surface_id_required"}
    svc = SurfaceService(session)
    try:
        return await svc.update(
            surface_id=surface_id,
            principal_id=principal_id,
            html=args.get("html"),
            title=args.get("title"),
            description=args.get("description"),
            source_note=args.get("source_note"),
            merge_state=args.get("merge_state") if isinstance(args.get("merge_state"), dict) else None,
            extend_hours=args.get("extend_hours"),
                    expected_revision=args.get("expected_revision"),
        )
    except Exception as e:
        logger.error("update_surface_failed", error=str(e))
        return {"ok": False, "error": "surface_update_failed", "detail": str(e)[:200]}


async def handle_list_surfaces(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """List this learner's surfaces for continuation / discovery."""
    from wax.surfaces.service import SurfaceService

    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    limit = int(args.get("limit") or 10)
    active_only = args.get("active_only", True)
    if isinstance(active_only, str):
        active_only = active_only.lower() not in ("0", "false", "no")
    rows = await SurfaceService(session).list_for_principal(
        principal_id, limit=limit, active_only=bool(active_only)
    )
    return {"ok": True, "surfaces": rows, "count": len(rows)}


async def handle_revoke_surface(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """Revoke a surface (access stops immediately; durable Work/Artifacts untouched)."""
    from wax.surfaces.service import SurfaceService

    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    surface_id = args.get("surface_id")
    if not surface_id:
        return {"ok": False, "error": "surface_id_required"}
    return await SurfaceService(session).revoke(surface_id, principal_id)




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
    "schedule": handle_schedule,
    "cancel_schedule": handle_cancel_schedule,
    "create_artifact": handle_create_artifact,
    "present_choices": handle_present_choices,
    "get_current_time": handle_get_current_time,
    "get_learner_state": handle_get_learner_state,
    "set_preference": handle_set_preference,
    "redeliver_artifact": handle_redeliver_artifact,
    "export_learner_data": handle_export_learner_data,
    "link_channel_identity": handle_link_channel_identity,
    "request_channel_link": handle_request_channel_link,
    "confirm_channel_link": handle_confirm_channel_link,
    "retain_surface": handle_retain_surface,
    "inspect_surface": handle_inspect_surface,
    "create_surface": handle_create_surface,
    "update_surface": handle_update_surface,
    "list_surfaces": handle_list_surfaces,
    "revoke_surface": handle_revoke_surface,
    "inspect_memories": handle_inspect_memories,
    "manage_goal": handle_manage_goal,
    "forget_memory": handle_forget_memory,
    "list_artifacts": handle_list_artifacts,
    "read_artifact": handle_read_artifact,
    "world_discover": handle_world_discover,
    "world_exec": handle_world_exec,
    "world_acquire": handle_world_acquire,
    "world_files": handle_world_files,
    "world_jobs": handle_world_jobs,
})
