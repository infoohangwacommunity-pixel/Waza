"""
Retention Intelligence — FSRS-inspired, not flashcard scheduling.

Tracks stability / difficulty / retrievability per learning target.
Predicts review *opportunities*; tutor decides how to use them.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import Concept, LearnerConceptState
from wax.observability.logging import get_logger

logger = get_logger(__name__)

# Desired retention probability target
DEFAULT_REQUEST_RETENTION = 0.9
# Minimum / maximum stability (days)
MIN_STABILITY = 0.4
MAX_STABILITY = 365.0


def retrievability(stability_days: float, elapsed_days: float) -> float:
    """R = 0.9^(t/S) — approximate exponential decay used by FSRS-family models."""
    if stability_days <= 0:
        return 0.0
    return float(0.9 ** (elapsed_days / stability_days))


def days_until_retrievability(stability_days: float, target_r: float = DEFAULT_REQUEST_RETENTION) -> float:
    if stability_days <= 0 or target_r <= 0 or target_r >= 1:
        return 1.0
    # 0.9^(t/S) = target => t/S = log(target)/log(0.9) => t = S * log(target)/log(0.9)
    return stability_days * (math.log(target_r) / math.log(0.9))


def update_after_review(
    *,
    stability: float,
    difficulty: float,
    grade: int,
    elapsed_days: float,
    assisted: bool = False,
) -> dict[str, float]:
    """
    grade: 1 again, 2 hard, 3 good, 4 easy (Anki-style).
    Assisted success is weaker than independent.
    """
    difficulty = max(0.01, min(0.99, difficulty))
    stability = max(MIN_STABILITY, stability)

    if grade <= 1:
        # lapse
        new_s = max(MIN_STABILITY, stability * 0.25)
        new_d = min(0.99, difficulty + 0.08)
        return {"stability": new_s, "difficulty": new_d, "lapse": 1.0}

    # success
    factor = {2: 1.2, 3: 2.0, 4: 2.8}.get(grade, 2.0)
    if assisted:
        factor *= 0.75
    # harder items grow slower
    factor *= 1.0 - 0.35 * difficulty
    # reward longer successful holds slightly
    if elapsed_days > stability * 0.8 and grade >= 3:
        factor *= 1.1
    new_s = min(MAX_STABILITY, max(MIN_STABILITY, stability * factor))
    new_d = max(0.01, difficulty - (0.04 if grade >= 3 else 0.01))
    return {"stability": new_s, "difficulty": new_d, "lapse": 0.0}


class RetentionService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def record_retrieval(
        self,
        *,
        principal_id,
        concept_key: str,
        success: bool,
        assisted: bool = False,
        grade: int | None = None,
        spontaneous: bool = False,
    ) -> dict[str, Any]:
        from wax.knowledge.graph import KnowledgeGraphService
        from sqlalchemy import select
        from wax.db.models import LearnerConceptState

        graph = KnowledgeGraphService(self.session)
        concept = await graph.ensure_concept(concept_key)
        stmt = select(LearnerConceptState).where(
            LearnerConceptState.principal_id == principal_id,
            LearnerConceptState.concept_id == concept.id,
        )
        state = (await self.session.execute(stmt)).scalar_one_or_none()
        if state is None:
            state = await graph.update_learner_state(
                principal_id, concept_key, mastery_delta=0.05, status="partial"
            )
        now = datetime.now(timezone.utc)
        stability = float(getattr(state, "stability", None) or 1.0)
        difficulty = float(getattr(state, "difficulty", None) or 0.3)
        last = getattr(state, "last_review_at", None) or getattr(state, "last_observed_at", None)
        elapsed = 0.5
        if last:
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            elapsed = max(0.01, (now - last).total_seconds() / 86400.0)

        if grade is None:
            if not success:
                grade = 1
            elif assisted:
                grade = 2
            elif spontaneous:
                grade = 4
            else:
                grade = 3

        upd = update_after_review(
            stability=stability,
            difficulty=difficulty,
            grade=grade,
            elapsed_days=elapsed,
            assisted=assisted,
        )
        state.stability = upd["stability"]
        state.difficulty = upd["difficulty"]
        state.last_review_at = now
        state.review_count = int(getattr(state, "review_count", 0) or 0) + 1
        if upd["lapse"]:
            state.lapse_count = int(getattr(state, "lapse_count", 0) or 0) + 1
        if success and not assisted:
            state.independent_successes = int(getattr(state, "independent_successes", 0) or 0) + 1
        elif success and assisted:
            state.assisted_successes = int(getattr(state, "assisted_successes", 0) or 0) + 1

        r = retrievability(state.stability, 0.0)
        state.retrievability = r
        interval = days_until_retrievability(state.stability)
        state.next_review_at = now + timedelta(days=interval)
        state.last_observed_at = now
        await self.session.flush()

        from wax.learner.events import record_learner_event

        await record_learner_event(
            self.session,
            principal_id=principal_id,
            kind="spontaneous_retrieval" if spontaneous else ("learning_success" if success else "learning_failure"),
            summary=f"Retrieval on {concept_key}: success={success} grade={grade}",
            concept_key=concept_key,
            payload={
                "stability": state.stability,
                "difficulty": state.difficulty,
                "next_review_at": state.next_review_at.isoformat(),
                "assisted": assisted,
                "spontaneous": spontaneous,
            },
            confidence=0.8,
        )
        return {
            "concept_key": concept_key,
            "stability": state.stability,
            "difficulty": state.difficulty,
            "retrievability": state.retrievability,
            "next_review_at": state.next_review_at.isoformat() if state.next_review_at else None,
            "lapse_count": state.lapse_count,
        }

    async def fragile_targets(
        self, principal_id, *, limit: int = 8, threshold_r: float = 0.85
    ) -> list[dict[str, Any]]:
        """Learning targets whose predicted retrievability is below threshold."""
        now = datetime.now(timezone.utc)
        stmt = (
            select(LearnerConceptState, Concept)
            .join(Concept, Concept.id == LearnerConceptState.concept_id)
            .where(LearnerConceptState.principal_id == principal_id)
            .order_by(LearnerConceptState.next_review_at.asc().nullsfirst())
            .limit(40)
        )
        rows = (await self.session.execute(stmt)).all()
        out = []
        for state, concept in rows:
            stability = float(state.stability or 1.0)
            last = state.last_review_at or state.last_observed_at
            elapsed = 0.0
            if last:
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                elapsed = max(0.0, (now - last).total_seconds() / 86400.0)
            r = retrievability(stability, elapsed)
            if r >= threshold_r and state.next_review_at and state.next_review_at > now:
                continue
            if float(state.mastery or 0) < 0.15 and state.review_count == 0:
                continue  # never really learned
            out.append(
                {
                    "concept_key": concept.key,
                    "label": concept.label,
                    "retrievability": round(r, 3),
                    "stability": stability,
                    "difficulty": float(state.difficulty or 0.3),
                    "mastery": float(state.mastery or 0),
                    "next_review_at": state.next_review_at.isoformat() if state.next_review_at else None,
                    "reason": "fragile" if r < threshold_r else "due",
                }
            )
            if len(out) >= limit:
                break
        out.sort(key=lambda x: x["retrievability"])
        return out
