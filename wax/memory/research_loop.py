"""
Longitudinal learner-state research loop.

Observe → hypothesize → design a light test → schedule/follow up → update confidence.

Not a curriculum engine. The tutor (or memory worker) may call this to
propose the next useful check for an active hypothesis.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import Hypothesis, ScheduledAction
from wax.observability.logging import get_logger

logger = get_logger(__name__)


class ResearchLoopService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def active_candidates(self, principal_id, limit: int = 5) -> list[Hypothesis]:
        stmt = (
            select(Hypothesis)
            .where(
                Hypothesis.principal_id == principal_id,
                Hypothesis.status.in_(["candidate", "active"]),
            )
            .order_by(Hypothesis.confidence.asc())  # least certain first
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def propose_next_test(self, principal_id) -> dict[str, Any] | None:
        """
        Return a suggestion the tutor can act on — never auto-spams the learner.
        """
        hyps = await self.active_candidates(principal_id, limit=3)
        if not hyps:
            return None
        hyp = hyps[0]
        return {
            "hypothesis_id": str(hyp.id),
            "claim_key": hyp.claim_key,
            "claim": hyp.claim,
            "status": hyp.status,
            "confidence": hyp.confidence,
            "suggestion": (
                "Design a short task or question that would support or contradict "
                f"this hypothesis: {hyp.claim[:200]}"
            ),
            "rationale": hyp.rationale,
        }

    async def schedule_hypothesis_recheck(
        self,
        *,
        principal_id,
        hypothesis_id,
        delay_hours: float = 24.0,
        message_hint: str | None = None,
    ) -> ScheduledAction | None:
        """Durable follow-up to re-test a hypothesis later."""
        hyp = await self.session.get(Hypothesis, hypothesis_id)
        if not hyp or hyp.principal_id != principal_id:
            return None
        run_at = datetime.now(timezone.utc) + timedelta(hours=float(delay_hours))
        action = ScheduledAction(
            id=uuid4(),
            principal_id=principal_id,
            action_type="hypothesis_recheck",
            status="pending",
            execute_at=run_at,
            reason="Longitudinal hypothesis recheck",
            payload={
                "hypothesis_id": str(hypothesis_id),
                "claim_key": hyp.claim_key,
                "message_hint": message_hint
                or f"Gently check understanding related to: {hyp.claim[:120]}",
            },
            idempotency_key=f"hyp-recheck:{hypothesis_id}:{run_at.date().isoformat()}",
        )
        self.session.add(action)
        try:
            await self.session.flush()
        except Exception:
            # unique conflict on idempotency — ok
            await self.session.rollback()
            return None
        logger.info(
            "hypothesis_recheck_scheduled",
            hypothesis_id=str(hypothesis_id),
            run_at=run_at.isoformat(),
        )
        return action

    async def record_test_outcome(
        self,
        *,
        principal_id,
        claim_key: str,
        supports: bool,
        description: str,
        assistance_level: str = "independent",
    ) -> dict[str, Any]:
        from wax.memory.learning_state import LearningStateService

        result = await LearningStateService(self.session).apply_performance(
            principal_id=principal_id,
            concept_key=claim_key,
            is_correct=supports,
            assistance_level=assistance_level,
            description=description,
            source="research_loop",
        )
        return result
