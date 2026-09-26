"""
LearnerModel — connected projection of who this person is right now.

Consumes existing Memory, Goals, Activities, Concept state, Schedule, Evidence.
Does not replace those tables; orchestrates them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import (
    Activity,
    Goal,
    LearningEvent,
    Memory,
    Principal,
    ScheduledAction,
)
from wax.domain.learner_state import build_learner_state_snapshot
from wax.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class LearnerModel:
    principal_id: str
    at: str
    identity: dict[str, Any] = field(default_factory=dict)
    goals: list[dict[str, Any]] = field(default_factory=list)
    activities: list[dict[str, Any]] = field(default_factory=list)
    preferences: list[dict[str, Any]] = field(default_factory=list)
    learning_targets: list[dict[str, Any]] = field(default_factory=list)
    fragile: list[dict[str, Any]] = field(default_factory=list)
    temporal_intents: list[dict[str, Any]] = field(default_factory=list)
    recent_events: list[dict[str, Any]] = field(default_factory=list)
    unresolved: list[dict[str, Any]] = field(default_factory=list)
    teaching: dict[str, Any] = field(default_factory=dict)
    timezone: str = "UTC"
    raw_snapshot: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "principal_id": self.principal_id,
            "at": self.at,
            "identity": self.identity,
            "goals": self.goals,
            "activities": self.activities,
            "preferences": self.preferences,
            "learning_targets": self.learning_targets[:12],
            "fragile": self.fragile,
            "temporal_intents": self.temporal_intents,
            "recent_events": self.recent_events[:15],
            "unresolved": self.unresolved,
            "teaching": self.teaching,
            "timezone": self.timezone,
        }


async def build_learner_model(
    session: AsyncSession,
    principal_id,
    *,
    include_retention: bool = True,
    include_events: bool = True,
) -> LearnerModel:
    now = datetime.now(timezone.utc)
    pid = principal_id
    model = LearnerModel(principal_id=str(pid), at=now.isoformat())

    principal = await session.get(Principal, pid)
    if principal:
        profile = getattr(principal, "profile", None) or {}
        prefs = getattr(principal, "preferences", None) or {}
        model.identity = {
            "profile": profile if isinstance(profile, dict) else {},
            "preferences_raw": prefs if isinstance(prefs, dict) else {},
        }
        model.timezone = (
            (prefs or {}).get("timezone")
            or (profile or {}).get("timezone")
            or "Africa/Lagos"
        )

    snap = await build_learner_state_snapshot(session, pid)
    model.raw_snapshot = snap
    model.goals = snap.get("goals") or []
    model.activities = snap.get("activities") or []

    # preference memories
    pref_stmt = (
        select(Memory)
        .where(
            Memory.principal_id == pid,
            Memory.memory_type.in_(["preference", "relationship", "semantic"]),
            Memory.is_active.is_(True),
        )
        .order_by(Memory.importance.desc(), Memory.updated_at.desc())
        .limit(12)
    )
    prefs = list((await session.execute(pref_stmt)).scalars().all())
    model.preferences = [
        {
            "id": str(m.id),
            "content": m.content,
            "type": m.memory_type,
            "confidence": float(m.confidence or 0),
            "importance": float(m.importance or 0),
            "updated_at": m.updated_at.isoformat() if m.updated_at else None,
        }
        for m in prefs
    ]

    if include_retention:
        try:
            from wax.learner.retention import RetentionService

            model.fragile = await RetentionService(session).fragile_targets(pid, limit=6)
        except Exception:
            logger.exception("retention_fragile_failed")

    # pending temporal / scheduled
    s_stmt = (
        select(ScheduledAction)
        .where(
            ScheduledAction.principal_id == pid,
            ScheduledAction.status == "pending",
            ScheduledAction.execute_at >= now,
        )
        .order_by(ScheduledAction.execute_at.asc())
        .limit(8)
    )
    scheduled = list((await session.execute(s_stmt)).scalars().all())
    model.temporal_intents = [
        {
            "id": str(a.id),
            "type": a.action_type,
            "reason": a.reason,
            "execute_at": a.execute_at.isoformat() if a.execute_at else None,
            "payload": a.payload or {},
        }
        for a in scheduled
    ]

    if include_events:
        try:
            e_stmt = (
                select(LearningEvent)
                .where(LearningEvent.principal_id == pid)
                .order_by(LearningEvent.observed_at.desc())
                .limit(20)
            )
            events = list((await session.execute(e_stmt)).scalars().all())
            model.recent_events = [
                {
                    "kind": e.kind,
                    "summary": e.summary,
                    "concept_key": e.concept_key,
                    "observed_at": e.observed_at.isoformat() if e.observed_at else None,
                }
                for e in events
            ]
        except Exception:
            # table may not exist yet before migration
            pass

    # teaching thread from active activity
    active = model.activities[0] if model.activities else None
    model.teaching = {
        "current_thread": active.get("objective") if active else None,
        "activity_status": active.get("status") if active else None,
        "activity_kind": active.get("kind") if active else None,
        "paused_threads": [
            a.get("objective") for a in model.activities[1:4] if a.get("status") == "paused"
        ],
        "fragile_count": len(model.fragile),
        "active_goals": [g.get("title") for g in model.goals[:3]],
    }
    if active and active.get("status") in ("active", "paused", "waiting"):
        model.unresolved.append(
            {
                "kind": "activity",
                "summary": active.get("objective") or active.get("kind"),
                "status": active.get("status"),
            }
        )
    return model
