"""
Time infrastructure — durable schedules and wakes.

AI decides what/when. Infrastructure stores, claims, and wakes Work.
Not a reminder product or educational workflow.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import ScheduledAction
from wax.observability.logging import get_logger
from wax.scheduler.service import SchedulerService

logger = get_logger(__name__)


def _as_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


async def get_current_time(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {
        "ok": True,
        "utc": now.isoformat(),
        "unix": int(now.timestamp()),
        "timezone": str(args.get("timezone") or "UTC"),
    }


def _resolve_execute_at(args: dict[str, Any], now: datetime) -> tuple[datetime | None, str | None]:
    """Parse delay_seconds / delay_minutes / delay_hours / execute_at → UTC datetime."""
    if args.get("execute_at"):
        raw = str(args["execute_at"])
        try:
            execute_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if execute_at.tzinfo is None:
                execute_at = execute_at.replace(tzinfo=timezone.utc)
            else:
                execute_at = execute_at.astimezone(timezone.utc)
            return execute_at, None
        except ValueError:
            return None, "invalid_execute_at"

    delay_sec: float | None = None
    if args.get("delay_seconds") is not None:
        try:
            delay_sec = float(args["delay_seconds"])
        except (TypeError, ValueError):
            return None, "invalid_delay_seconds"
    elif args.get("delay_minutes") is not None:
        try:
            delay_sec = float(args["delay_minutes"]) * 60.0
        except (TypeError, ValueError):
            return None, "invalid_delay_minutes"
    elif args.get("delay_hours") is not None:
        try:
            delay_sec = float(args["delay_hours"]) * 3600.0
        except (TypeError, ValueError):
            return None, "invalid_delay_hours"
    elif args.get("delay") is not None:
        # bare delay treated as seconds
        try:
            delay_sec = float(args["delay"])
        except (TypeError, ValueError):
            return None, "invalid_delay"

    if delay_sec is not None:
        # Sub-second floors still wake; min 0.5s for DB/worker practicality
        return now + timedelta(seconds=max(0.5, delay_sec)), None
    return None, "execute_at_or_delay_required"


async def schedule_action(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """
    Schedule a future wake. Supports:
      delay_seconds, delay_minutes, delay_hours, execute_at (ISO)
      optional series: interval_hours + count (finite; capped for DB safety)
    """
    principal_id = ctx.get("principal_id")
    if not principal_id:
        return {"ok": False, "error": "no_principal"}

    reason = str(args.get("reason") or args.get("message_hint") or args.get("objective") or "scheduled")[:500]
    message_hint = str(args.get("message_hint") or args.get("objective") or reason)[:2000]
    action_type = str(args.get("action_type") or "wake")[:80]

    now = datetime.now(timezone.utc)
    execute_at, err = _resolve_execute_at(args, now)
    if err or execute_at is None:
        return {"ok": False, "error": err or "execute_at_or_delay_required"}

    svc = SchedulerService(session)
    payload = {
        "message_hint": message_hint,
        "channel": ctx.get("channel"),
        "target_external_id": ctx.get("target_external_id"),
        "conversation_id": str(ctx["conversation_id"]) if ctx.get("conversation_id") else None,
        "objective": message_hint,
    }

    # Finite series if requested
    count = args.get("count") or args.get("occurrences")
    interval_hours = args.get("interval_hours")
    interval_seconds = args.get("interval_seconds")
    if interval_seconds is not None and interval_hours is None:
        try:
            interval_hours = float(interval_seconds) / 3600.0
        except (TypeError, ValueError):
            return {"ok": False, "error": "invalid_interval_seconds"}

    if count is not None and interval_hours is not None:
        try:
            n = int(count)
            ih = float(interval_hours)
        except (TypeError, ValueError):
            return {"ok": False, "error": "invalid_series_params"}
        actions = await svc.schedule_series(
            principal_id=principal_id,
            action_type=action_type,
            first_at=execute_at,
            interval_hours=ih,
            count=n,
            reason=reason,
            payload=payload,
        )
        return {
            "ok": True,
            "series": True,
            "count": len(actions),
            "scheduled_action_ids": [str(a.id) for a in actions],
            "first_execute_at": actions[0].execute_at.isoformat() if actions else None,
            "reason": reason,
        }

    action = await svc.schedule(
        principal_id=principal_id,
        action_type=action_type,
        execute_at=execute_at,
        reason=reason,
        payload=payload,
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
    principal_id = _as_uuid(ctx.get("principal_id"))
    action_id = _as_uuid(args.get("scheduled_action_id") or args.get("id") or args.get("action_id"))
    if not principal_id or not action_id:
        return {"ok": False, "error": "scheduled_action_id_required"}
    svc = SchedulerService(session)
    action = await svc.cancel(action_id, principal_id=principal_id)
    if not action:
        return {"ok": False, "error": "not_found"}
    return {"ok": True, "scheduled_action_id": str(action.id), "status": action.status}


async def list_scheduled(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    principal_id = _as_uuid(ctx.get("principal_id"))
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    limit = max(1, min(int(args.get("limit") or 15), 40))
    include_done = bool(args.get("include_done"))
    statuses = ["pending"]
    if include_done:
        statuses.extend(["executing", "completed", "cancelled", "failed"])
    stmt = (
        select(ScheduledAction)
        .where(
            ScheduledAction.principal_id == principal_id,
            ScheduledAction.status.in_(statuses if include_done else ["pending"]),
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


async def handle_time_directive(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """
    Dispatch ```time channel actions.
    action: now | schedule | cancel | list | series
    Default action is schedule when delay/execute_at present.
    """
    action = str(args.get("action") or "").strip().lower()
    if not action:
        if str(args.get("cancel") or "").lower() in ("1", "true", "yes") or str(args.get("action") or "").lower() == "cancel":
            action = "cancel"
        elif any(k in args for k in ("delay_seconds", "delay_minutes", "delay_hours", "delay", "execute_at", "count", "interval_hours", "interval_seconds")):
            action = "schedule"
        elif str(args.get("raw") or "").strip().lower() in ("now", "time", "current"):
            action = "now"
        else:
            # free-form body might be "now" or a delay
            raw = str(args.get("raw") or "").strip().lower()
            if raw in ("now", "time", "current time", "utc"):
                action = "now"
            elif raw.startswith("cancel"):
                action = "cancel"
            elif raw.startswith("list"):
                action = "list"
            else:
                action = "schedule"

    if action in ("now", "current", "time"):
        return await get_current_time(session, args, ctx)
    if action in ("cancel", "delete"):
        return await cancel_schedule(session, args, ctx)
    if action in ("list", "pending"):
        return await list_scheduled(session, args, ctx)
    if action in ("schedule", "series", "create", "set"):
        return await schedule_action(session, args, ctx)
    return {"ok": False, "error": f"unknown_time_action:{action}"}
