"""
First-class Teaching State — durable learning thread, not activity-row projection alone.

Threads can pause/branch/resume without subject hardcoding.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import Activity
from wax.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class TeachingState:
    principal_id: str
    thread_id: str
    topic: str = ""
    goal_hint: str = ""
    status: str = "active"  # active|paused|branched|completed
    explained: list[str] = field(default_factory=list)
    understood: list[str] = field(default_factory=list)
    uncertain: list[str] = field(default_factory=list)
    misconceptions: list[str] = field(default_factory=list)
    prerequisites_missing: list[str] = field(default_factory=list)
    last_move: str = ""
    planned_next: str = ""
    parent_thread_id: str | None = None
    updated_at: str = ""

    def summary_line(self) -> str:
        parts = [
            f"Thread[{self.status}]: {self.topic or 'unspecified'}",
        ]
        if self.explained:
            parts.append(f"explained={self.explained[-3:]}")
        if self.uncertain:
            parts.append(f"uncertain={self.uncertain[-3:]}")
        if self.prerequisites_missing:
            parts.append(f"prereq_gaps={self.prerequisites_missing[-3:]}")
        if self.planned_next:
            parts.append(f"next={self.planned_next[:120]}")
        if self.parent_thread_id:
            parts.append("branched_from_parent")
        return "; ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "topic": self.topic,
            "goal_hint": self.goal_hint,
            "status": self.status,
            "explained": self.explained[-12:],
            "understood": self.understood[-12:],
            "uncertain": self.uncertain[-12:],
            "misconceptions": self.misconceptions[-8:],
            "prerequisites_missing": self.prerequisites_missing[-8:],
            "last_move": self.last_move,
            "planned_next": self.planned_next,
            "parent_thread_id": self.parent_thread_id,
            "updated_at": self.updated_at,
        }


def _state_from_activity(activity: Activity) -> TeachingState | None:
    meta = activity.metadata_ if isinstance(getattr(activity, "metadata_", None), dict) else {}
    ts = meta.get("teaching_state")
    if not isinstance(ts, dict):
        return None
    return TeachingState(
        principal_id=str(activity.principal_id),
        thread_id=str(ts.get("thread_id") or activity.id),
        topic=str(ts.get("topic") or activity.objective or ""),
        goal_hint=str(ts.get("goal_hint") or ""),
        status=str(ts.get("status") or activity.status or "active"),
        explained=list(ts.get("explained") or []),
        understood=list(ts.get("understood") or []),
        uncertain=list(ts.get("uncertain") or []),
        misconceptions=list(ts.get("misconceptions") or []),
        prerequisites_missing=list(ts.get("prerequisites_missing") or []),
        last_move=str(ts.get("last_move") or ""),
        planned_next=str(ts.get("planned_next") or ""),
        parent_thread_id=ts.get("parent_thread_id"),
        updated_at=str(ts.get("updated_at") or ""),
    )


async def load_teaching_state(session: AsyncSession, principal_id) -> TeachingState | None:
    stmt = (
        select(Activity)
        .where(
            Activity.principal_id == principal_id,
            Activity.status.in_(["active", "paused", "waiting"]),
        )
        .order_by(Activity.updated_at.desc())
        .limit(5)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    for a in rows:
        ts = _state_from_activity(a)
        if ts:
            return ts
    # synthesize thin state from activity objective
    if rows:
        a = rows[0]
        return TeachingState(
            principal_id=str(principal_id),
            thread_id=str(a.id),
            topic=(a.objective or a.kind or "")[:300],
            status=a.status or "active",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
    return None


async def save_teaching_state(
    session: AsyncSession,
    principal_id,
    state: TeachingState,
    *,
    activity_id=None,
) -> TeachingState:
    state.updated_at = datetime.now(timezone.utc).isoformat()
    activity = None
    if activity_id:
        activity = await session.get(Activity, activity_id)
    if activity is None:
        stmt = (
            select(Activity)
            .where(
                Activity.principal_id == principal_id,
                Activity.status.in_(["active", "paused", "waiting"]),
            )
            .order_by(Activity.updated_at.desc())
            .limit(1)
        )
        activity = (await session.execute(stmt)).scalar_one_or_none()
    if activity is None:
        activity = Activity(
            id=uuid.uuid4(),
            principal_id=principal_id,
            kind="learning_thread",
            status=state.status if state.status in ("active", "paused") else "active",
            objective=state.topic[:500] if state.topic else "learning",
            metadata_={},
        )
        session.add(activity)
    meta = dict(activity.metadata_ or {}) if isinstance(activity.metadata_, dict) else {}
    meta["teaching_state"] = state.to_dict()
    activity.metadata_ = meta
    activity.objective = state.topic[:500] if state.topic else activity.objective
    activity.status = "paused" if state.status == "paused" else (
        "active" if state.status in ("active", "branched") else activity.status
    )
    activity.updated_at = datetime.now(timezone.utc)
    await session.flush()
    logger.info(
        "teaching_state_saved",
        principal_id=str(principal_id),
        thread_id=state.thread_id,
        status=state.status,
        topic=state.topic[:80],
    )
    return state


async def pause_thread(session: AsyncSession, principal_id, *, reason: str = "") -> TeachingState | None:
    ts = await load_teaching_state(session, principal_id)
    if not ts:
        return None
    ts.status = "paused"
    ts.last_move = f"paused:{reason}"[:200]
    return await save_teaching_state(session, principal_id, ts)


async def branch_thread(
    session: AsyncSession,
    principal_id,
    *,
    new_topic: str,
    reason: str = "learner_request",
) -> TeachingState:
    parent = await load_teaching_state(session, principal_id)
    if parent and parent.status == "active":
        parent.status = "paused"
        parent.last_move = f"branched_for:{new_topic}"[:200]
        await save_teaching_state(session, principal_id, parent)
    child = TeachingState(
        principal_id=str(principal_id),
        thread_id=str(uuid.uuid4()),
        topic=new_topic[:300],
        status="active",
        parent_thread_id=parent.thread_id if parent else None,
        last_move=f"branch:{reason}"[:200],
        planned_next=f"return to {parent.topic}" if parent else "",
    )
    return await save_teaching_state(session, principal_id, child)


async def update_from_turn(
    session: AsyncSession,
    principal_id,
    *,
    topic: str | None = None,
    explained: str | None = None,
    understood: str | None = None,
    uncertain: str | None = None,
    misconception: str | None = None,
    prereq_gap: str | None = None,
    planned_next: str | None = None,
    last_move: str | None = None,
) -> TeachingState:
    ts = await load_teaching_state(session, principal_id)
    if ts is None:
        ts = TeachingState(
            principal_id=str(principal_id),
            thread_id=str(uuid.uuid4()),
            topic=topic or "",
            status="active",
        )
    if topic:
        ts.topic = topic[:300]
    if explained:
        ts.explained = (ts.explained + [explained[:200]])[-20:]
    if understood:
        ts.understood = (ts.understood + [understood[:200]])[-20:]
    if uncertain:
        ts.uncertain = (ts.uncertain + [uncertain[:200]])[-20:]
    if misconception:
        ts.misconceptions = (ts.misconceptions + [misconception[:200]])[-12:]
    if prereq_gap:
        ts.prerequisites_missing = (ts.prerequisites_missing + [prereq_gap[:200]])[-12:]
    if planned_next is not None:
        ts.planned_next = planned_next[:300]
    if last_move:
        ts.last_move = last_move[:200]
    ts.status = "active"
    return await save_teaching_state(session, principal_id, ts)
