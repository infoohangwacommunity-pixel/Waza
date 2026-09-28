"""
Minimal infrastructure primitives for the AI.

Not a product tool menu. Not educational workflows.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from wax.intelligence.providers import ToolSpec
from wax.observability.logging import get_logger
from wax.primitives import memory as mem
from wax.primitives import schedule as sched
from wax.primitives import world_ops
from wax.primitives.interaction_ops import present_choices
from wax.primitives.publish import publish_surface, revoke_surface, update_surface

logger = get_logger(__name__)

Handler = Callable[[AsyncSession, dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]]


def _parse_args(raw: str | dict | None) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


async def _memory_search(session, args, ctx):
    return await mem.memory_search(
        session,
        ctx.get("principal_id"),
        query=args.get("query"),
        memory_type=args.get("memory_type"),
        limit=int(args.get("limit") or 20),
        include_inactive=bool(args.get("include_inactive")),
    )


async def _memory_get(session, args, ctx):
    return await mem.memory_get(session, ctx.get("principal_id"), str(args.get("memory_id") or ""))


async def _memory_create(session, args, ctx):
    return await mem.memory_create(
        session,
        ctx.get("principal_id"),
        content=str(args.get("content") or ""),
        memory_type=str(args.get("memory_type") or "semantic"),
        confidence=float(args.get("confidence") or 0.7),
        importance=float(args.get("importance") or 0.5),
        tags=args.get("tags"),
        structured=args.get("structured"),
        work_id=ctx.get("work_id"),
    )


async def _memory_update(session, args, ctx):
    return await mem.memory_update(
        session,
        ctx.get("principal_id"),
        str(args.get("memory_id") or ""),
        content=args.get("content"),
        confidence=args.get("confidence"),
        importance=args.get("importance"),
        tags=args.get("tags"),
        structured=args.get("structured"),
    )


async def _memory_supersede(session, args, ctx):
    return await mem.memory_supersede(
        session,
        ctx.get("principal_id"),
        str(args.get("old_memory_id") or args.get("memory_id") or ""),
        new_content=str(args.get("new_content") or ""),
        reason=args.get("reason"),
        memory_type=args.get("memory_type"),
        work_id=ctx.get("work_id"),
    )


async def _memory_forget(session, args, ctx):
    return await mem.memory_forget(
        session,
        ctx.get("principal_id"),
        memory_id=str(args["memory_id"]) if args.get("memory_id") else None,
        query=args.get("query"),
    )


async def _set_preference(session, args, ctx):
    principal_id = ctx.get("principal_id")
    key = args.get("key")
    if not principal_id or not key:
        return {"ok": False, "error": "key_required"}
    from wax.domain.preferences import update_preferences

    await update_preferences(session, principal_id, {str(key): args.get("value")})
    return {"ok": True, "key": key, "value": args.get("value")}


HANDLERS: dict[str, Handler] = {
    "memory_search": _memory_search,
    "memory_get": _memory_get,
    "memory_create": _memory_create,
    "memory_update": _memory_update,
    "memory_supersede": _memory_supersede,
    "memory_forget": _memory_forget,
    "world_discover": world_ops.world_discover,
    "world_exec": world_ops.world_exec,
    "world_acquire": world_ops.world_acquire,
    "world_files": world_ops.world_files,
    "get_current_time": sched.get_current_time,
    "schedule": sched.schedule_action,
    "cancel_schedule": sched.cancel_schedule,
    "list_scheduled": sched.list_scheduled,
    "present_choices": present_choices,
    "publish": publish_surface,
    "update_surface": update_surface,
    "revoke_surface": revoke_surface,
    "set_preference": _set_preference,
}


PRIMITIVE_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="memory_search",
        description="Search this student's durable memories. You decide the query and what is relevant.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "memory_type": {"type": "string"},
                "limit": {"type": "number"},
                "include_inactive": {"type": "boolean"},
            },
        },
    ),
    ToolSpec(
        name="memory_get",
        description="Get one memory by id.",
        parameters={
            "type": "object",
            "properties": {"memory_id": {"type": "string"}},
            "required": ["memory_id"],
        },
    ),
    ToolSpec(
        name="memory_create",
        description="Persist something important about this student. You decide what matters.",
        parameters={
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "memory_type": {"type": "string"},
                "confidence": {"type": "number"},
                "importance": {"type": "number"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["content"],
        },
    ),
    ToolSpec(
        name="memory_update",
        description="Update an existing memory in place.",
        parameters={
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
                "content": {"type": "string"},
                "confidence": {"type": "number"},
                "importance": {"type": "number"},
            },
            "required": ["memory_id"],
        },
    ),
    ToolSpec(
        name="memory_supersede",
        description="Replace outdated memory with new content (marks old superseded).",
        parameters={
            "type": "object",
            "properties": {
                "old_memory_id": {"type": "string"},
                "new_content": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["old_memory_id", "new_content"],
        },
    ),
    ToolSpec(
        name="memory_forget",
        description="Forget a memory by id or by matching query when the student asks.",
        parameters={
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
                "query": {"type": "string"},
            },
        },
    ),
    ToolSpec(
        name="world_discover",
        description="Inspect the student's World: lifecycle, files, runtimes, installed packages.",
        parameters={"type": "object", "properties": {"sections": {"type": "string"}}},
    ),
    ToolSpec(
        name="world_exec",
        description="Run a command (argv) or Python script inside the student's isolated World.",
        parameters={
            "type": "object",
            "properties": {
                "argv": {"type": "array", "items": {"type": "string"}},
                "script": {"type": "string"},
                "cwd": {"type": "string"},
                "network_mode": {"type": "string"},
            },
        },
    ),
    ToolSpec(
        name="world_acquire",
        description="Install a package into the student's World (e.g. python_package via pip).",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "kind": {"type": "string"},
                "version_spec": {"type": "string"},
            },
            "required": ["name"],
        },
    ),
    ToolSpec(
        name="world_files",
        description="List/read/write/delete files in the student's World.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
        },
    ),
    ToolSpec(
        name="get_current_time",
        description="Authoritative current UTC time.",
        parameters={"type": "object", "properties": {"timezone": {"type": "string"}}},
    ),
    ToolSpec(
        name="schedule",
        description="Schedule a future wake. Use delay_seconds (e.g. 5), delay_hours, or execute_at ISO.",
        parameters={
            "type": "object",
            "properties": {
                "delay_seconds": {"type": "number"},
                "delay_hours": {"type": "number"},
                "execute_at": {"type": "string"},
                "reason": {"type": "string"},
                "message_hint": {"type": "string"},
                "action_type": {"type": "string"},
            },
        },
    ),
    ToolSpec(
        name="cancel_schedule",
        description="Cancel a pending scheduled action.",
        parameters={
            "type": "object",
            "properties": {"scheduled_action_id": {"type": "string"}},
            "required": ["scheduled_action_id"],
        },
    ),
    ToolSpec(
        name="list_scheduled",
        description="List pending scheduled actions for this student.",
        parameters={"type": "object", "properties": {"limit": {"type": "number"}}},
    ),
    ToolSpec(
        name="present_choices",
        description="Show interactive choices (server-authoritative). Optional expires_in_seconds.",
        parameters={
            "type": "object",
            "properties": {
                "choices": {"type": "array"},
                "prompt": {"type": "string"},
                "style": {"type": "string"},
                "expires_in_seconds": {"type": "number"},
            },
            "required": ["choices"],
        },
    ),
    ToolSpec(
        name="publish",
        description="Publish HTML you authored as a temporary secure web page. Returns page_url.",
        parameters={
            "type": "object",
            "properties": {
                "html": {"type": "string"},
                "title": {"type": "string"},
                "preferred_lifetime_hours": {"type": "number"},
            },
            "required": ["html"],
        },
    ),
    ToolSpec(
        name="update_surface",
        description="Update an existing published surface in place.",
        parameters={
            "type": "object",
            "properties": {
                "surface_id": {"type": "string"},
                "html": {"type": "string"},
                "title": {"type": "string"},
            },
            "required": ["surface_id"],
        },
    ),
    ToolSpec(
        name="revoke_surface",
        description="Revoke a published surface.",
        parameters={
            "type": "object",
            "properties": {"surface_id": {"type": "string"}},
            "required": ["surface_id"],
        },
    ),
    ToolSpec(
        name="set_preference",
        description="Store an explicit durable preference (e.g. message_length, language).",
        parameters={
            "type": "object",
            "properties": {"key": {"type": "string"}, "value": {}},
            "required": ["key", "value"],
        },
    ),
]


def list_primitive_specs() -> list[ToolSpec]:
    return list(PRIMITIVE_SPECS)


async def execute_primitive(
    session: AsyncSession,
    name: str,
    arguments: str | dict | None,
    ctx: dict[str, Any],
) -> dict[str, Any]:
    handler = HANDLERS.get(name)
    if not handler:
        return {"ok": False, "error": f"unknown_primitive:{name}"}
    args = _parse_args(arguments)
    try:
        outcome = await handler(session, args, ctx)
        logger.info("primitive_executed", name=name, ok=outcome.get("ok") if isinstance(outcome, dict) else None)
        return outcome if isinstance(outcome, dict) else {"ok": True, "result": outcome}
    except Exception as e:
        logger.exception("primitive_failed", name=name)
        return {"ok": False, "error": str(e)[:800]}
