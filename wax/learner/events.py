"""Durable learner events — the longitudinal backbone connecting all subsystems."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from wax.observability.logging import get_logger

logger = get_logger(__name__)

# Event kinds (not subject-specific)
EVENT_KINDS = frozenset(
    {
        "profile_updated",
        "goal_created",
        "goal_completed",
        "goal_abandoned",
        "preference_observed",
        "preference_confirmed",
        "preference_corrected",
        "concept_introduced",
        "concept_practiced",
        "concept_retrieved",
        "concept_forgotten",
        "concept_mastery_changed",
        "misconception_detected",
        "misconception_resolved",
        "material_added",
        "material_used",
        "schedule_created",
        "schedule_fulfilled",
        "schedule_deferred",
        "schedule_suppressed",
        "activity_started",
        "activity_paused",
        "activity_completed",
        "session_reentered",
        "learning_success",
        "learning_failure",
        "spontaneous_retrieval",
        "reminder_requested",
        "correction",
    }
)


async def record_learner_event(
    session: AsyncSession,
    *,
    principal_id,
    kind: str,
    summary: str = "",
    payload: dict[str, Any] | None = None,
    concept_key: str | None = None,
    goal_id=None,
    activity_id=None,
    evidence_id=None,
    memory_id=None,
    scheduled_action_id=None,
    work_id=None,
    confidence: float = 0.7,
) -> Any:
    """Persist a LearningEvent. Uses model when available; otherwise evidence-backed log."""
    from wax.db.models import LearningEvent

    if kind not in EVENT_KINDS:
        # allow forward-compatible kinds
        logger.info("learner_event_unknown_kind", kind=kind)

    row = LearningEvent(
        principal_id=principal_id,
        kind=kind[:80],
        summary=(summary or "")[:2000],
        payload=payload or {},
        concept_key=(concept_key or "")[:500] or None,
        goal_id=goal_id,
        activity_id=activity_id,
        evidence_id=evidence_id,
        memory_id=memory_id,
        scheduled_action_id=scheduled_action_id,
        work_id=work_id,
        confidence=max(0.0, min(1.0, float(confidence))),
        observed_at=datetime.now(timezone.utc),
    )
    session.add(row)
    await session.flush()
    logger.info(
        "learner_event_recorded",
        principal_id=str(principal_id),
        kind=kind,
        event_id=str(row.id),
    )
    return row
