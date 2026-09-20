"""Learner state snapshot — current situation, not a curriculum.

Assembled from durable tables for the tutor context.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import Activity, Goal, Interaction, ScheduledAction
from wax.observability.logging import get_logger

logger = get_logger(__name__)


async def build_learner_state_snapshot(
    session: AsyncSession, principal_id
) -> dict[str, Any]:
    if not principal_id:
        return {}

    now = datetime.now(timezone.utc)
    out: dict[str, Any] = {"at": now.isoformat()}

    # Active goals
    g_stmt = (
        select(Goal)
        .where(Goal.principal_id == principal_id, Goal.status.in_(["active", "open", "in_progress"]))
        .order_by(Goal.updated_at.desc())
        .limit(5)
    )
    goals = list((await session.execute(g_stmt)).scalars().all())
    out["goals"] = [
        {
            "id": str(g.id),
            "title": getattr(g, "title", None) or getattr(g, "description", None) or str(g.id),
            "status": g.status,
        }
        for g in goals
    ]

    # Active / paused activities (task stack)
    a_stmt = (
        select(Activity)
        .where(
            Activity.principal_id == principal_id,
            Activity.status.in_(["active", "paused", "waiting"]),
        )
        .order_by(Activity.updated_at.desc())
        .limit(5)
    )
    activities = list((await session.execute(a_stmt)).scalars().all())
    out["activities"] = [
        {
            "id": str(a.id),
            "kind": a.kind,
            "status": a.status,
            "objective": (a.objective or "")[:200],
            "ends_at": a.ends_at.isoformat() if a.ends_at else None,
        }
        for a in activities
    ]
    out["active_activity"] = out["activities"][0] if out["activities"] else None

    # Pending interactions
    i_stmt = (
        select(Interaction)
        .where(
            Interaction.principal_id == principal_id,
            Interaction.status == "pending",
        )
        .order_by(Interaction.created_at.desc())
        .limit(5)
    )
    pending = list((await session.execute(i_stmt)).scalars().all())
    out["pending_interactions"] = [
        {
            "id": str(ix.id),
            "channel": ix.channel,
            "expires_at": ix.expires_at.isoformat() if ix.expires_at else None,
            "n_choices": len(ix.choices or []),
        }
        for ix in pending
    ]

    # Upcoming scheduled actions
    s_stmt = (
        select(ScheduledAction)
        .where(
            ScheduledAction.principal_id == principal_id,
            ScheduledAction.status == "pending",
            ScheduledAction.execute_at >= now,
        )
        .order_by(ScheduledAction.execute_at.asc())
        .limit(5)
    )
    sched = list((await session.execute(s_stmt)).scalars().all())
    out["upcoming_schedule"] = [
        {
            "id": str(s.id),
            "action_type": s.action_type,
            "execute_at": s.execute_at.isoformat() if s.execute_at else None,
            "reason": (s.reason or "")[:120],
        }
        for s in sched
    ]

    return out


def format_learner_state_block(snap: dict[str, Any]) -> str:
    if not snap:
        return ""
    lines = ["\n--- Current learner situation ---"]
    if snap.get("active_activity"):
        a = snap["active_activity"]
        lines.append(
            f"Active activity: {a.get('kind')} [{a.get('status')}] "
            f"— {a.get('objective') or 'no objective'}"
        )
        if a.get("ends_at"):
            lines.append(f"  ends_at: {a['ends_at']}")
    elif snap.get("activities"):
        lines.append("Activities (paused/waiting):")
        for a in snap["activities"][:3]:
            lines.append(f"  - {a.get('kind')} [{a.get('status')}] {a.get('objective')}")
    if snap.get("goals"):
        lines.append("Goals:")
        for g in snap["goals"][:3]:
            lines.append(f"  - {g.get('title')} [{g.get('status')}]")
    if snap.get("pending_interactions"):
        lines.append(
            f"Pending interactive choices: {len(snap['pending_interactions'])}"
        )
    if snap.get("upcoming_schedule"):
        lines.append("Upcoming schedule:")
        for s in snap["upcoming_schedule"][:3]:
            lines.append(
                f"  - {s.get('action_type')} at {s.get('execute_at')}: {s.get('reason')}"
            )
    lines.append(
        "If the learner's new message is unrelated to the active activity, "
        "consider offering to pause and resume later — do not invent subject bans."
    )
    lines.append("--- End situation ---\n")
    return "\n".join(lines)
