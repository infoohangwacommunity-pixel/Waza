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


HANDLERS: dict[str, ToolHandler] = {
    "schedule_followup": handle_schedule_followup,
    "create_artifact": handle_create_artifact,
    "run_python": handle_run_python,
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
