"""
Time and schedule primitives — infrastructure owns reliable wake; AI decides what/when.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import ScheduledAction
from wax.observability.logging import get_logger
from wax.scheduler.service import SchedulerService

logger = get_logger(__name__)


async def get_current_time(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    tz_name = args.get("timezone") or "UTC"
    return {
        "ok": True,
        "utc": now.isoformat(),
        "unix": int(now.timestamp()),
        "timezone": tz_name,
    }


async def schedule_action(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """
    Schedule a future wake. Supports delay_seconds, delay_hours, or execute_at (ISO).
    Five seconds is valid. Infrastructure will create Work when due.
    """
    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    reason = str(args.get("reason") or args.get("message_hint") or "scheduled")[:500]
    message_hint = str(args.get("message_hint") or reason)[:2000]
    action_type = str(args.get("action_type") or "followup")[:80]

    now = datetime.now(timezone.utc)
    execute_at: datetime | None = None

    if args.get("execute_at"):
        raw = str(args["execute_at"])
        try:
            execute_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if execute_at.tzinfo is None:
                execute_at = execute_at.replace(tzinfo=timezone.utc)
        except ValueError:
            return {"ok": False, "error": "invalid_execute_at"}
    elif args.get("delay_seconds") is not None:
        try:
            secs = float(args["delay_seconds"])
        except (TypeError, ValueError):
            return {"ok": False, "error": "invalid_delay_seconds"}
        execute_at = now + timedelta(seconds=max(0.5, secs))
    elif args.get("delay_hours") is not None:
        try:
            hours = float(args["delay_hours"])
        except (TypeError, ValueError):
            return {"ok": False, "error": "invalid_delay_hours"}
        execute_at = now + timedelta(hours=max(0.0001, hours))
    else:
        return {"ok": False, "error": "execute_at_or_delay_required"}

    svc = SchedulerService(session)
    action = await svc.schedule(
        principal_id=principal_id,
        action_type=action_type,
        execute_at=execute_at,
        reason=reason,
        payload={
            "message_hint": message_hint,
            "channel": ctx.get("channel"),
            "target_external_id": ctx.get("target_external_id"),
            "conversation_id": str(ctx["conversation_id"]) if ctx.get("conversation_id") else None,
        },
    )
    return {
        "ok": True,
        "scheduled_action_id": str(action.id),
        "execute_at": action.execute_at.isoformat() if action.execute_at else None,
        "action_type": action.action_type,
        "reason": reason,
    }


async def cancel_schedule(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    principal_id = ctx.get("principal_id")
    action_id = args.get("scheduled_action_id")
    if not principal_id or not action_id:
        return {"ok": False, "error": "scheduled_action_id_required"}
    action = await session.get(ScheduledAction, action_id)
    if not action or action.principal_id != principal_id:
        return {"ok": False, "error": "not_found"}
    if action.status != "pending":
        return {"ok": False, "error": f"not_pending:{action.status}"}
    action.status = "cancelled"
    await session.flush()
    return {"ok": True, "scheduled_action_id": str(action.id), "status": "cancelled"}


async def list_scheduled(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    limit = max(1, min(int(args.get("limit") or 15), 40))
    stmt = (
        select(ScheduledAction)
        .where(
            ScheduledAction.principal_id == principal_id,
            ScheduledAction.status == "pending",
        )
        .order_by(ScheduledAction.execute_at.asc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    return {
        "ok": True,
        "count": len(rows),
        "actions": [
            {
                "id": str(a.id),
                "action_type": a.action_type,
                "reason": a.reason,
                "execute_at": a.execute_at.isoformat() if a.execute_at else None,
                "status": a.status,
            }
            for a in rows
        ],
    }
