"""
Generic assessment infrastructure — durable, timed optional, one-at-a-time optional.
Not QuizMode. Intelligence decides when and how to use it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import (
    Assessment,
    AssessmentAttempt,
    AssessmentItem,
    AssessmentResponse,
)
from wax.observability.logging import get_logger
from wax.work.activities import ActivityService
from wax.memory.evidence import EvidenceService

logger = get_logger(__name__)


class AssessmentService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        *,
        principal_id,
        title: str,
        objective: str | None = None,
        items: list[dict[str, Any]],
        timed: bool = False,
        duration_seconds: int | None = None,
        conversation_id=None,
        one_at_a_time: bool = True,
    ) -> Assessment:
        assessment = Assessment(
            id=uuid4(),
            principal_id=principal_id,
            conversation_id=conversation_id,
            title=title[:500],
            objective=objective,
            status="draft",
            timed=timed,
            duration_seconds=duration_seconds,
            settings={"one_at_a_time": one_at_a_time},
        )
        self.session.add(assessment)
        await self.session.flush()

        for i, raw in enumerate(items):
            item = AssessmentItem(
                id=uuid4(),
                assessment_id=assessment.id,
                ordinal=i,
                item_type=(raw.get("item_type") or raw.get("type") or "free_text")[:40],
                prompt=raw.get("prompt") or raw.get("question") or "",
                options=raw.get("options") or [],
                answer_key=raw.get("answer_key") or {},
                points=float(raw.get("points") or 1.0),
            )
            self.session.add(item)

        # Link durable activity for timed survival
        activity = await ActivityService(self.session).start(
            principal_id=principal_id,
            kind="assessment",
            objective=objective or title,
            duration_seconds=duration_seconds if timed else None,
            content={"assessment_id": str(assessment.id)},
            conversation_id=conversation_id,
        )
        assessment.activity_id = activity.id
        assessment.status = "active"
        now = datetime.now(timezone.utc)
        assessment.started_at = now
        if timed and duration_seconds:
            assessment.ends_at = now + timedelta(seconds=duration_seconds)
        await self.session.flush()
        logger.info("assessment_created", assessment_id=str(assessment.id), items=len(items))
        return assessment

    async def start_attempt(self, assessment_id, principal_id) -> AssessmentAttempt:
        assessment = await self.session.get(Assessment, assessment_id)
        if not assessment or assessment.principal_id != principal_id:
            raise ValueError("assessment_not_found")
        if assessment.status not in ("active", "draft", "paused"):
            raise ValueError(f"assessment_not_active:{assessment.status}")
        attempt = AssessmentAttempt(
            id=uuid4(),
            assessment_id=assessment.id,
            principal_id=principal_id,
            status="in_progress",
            started_at=datetime.now(timezone.utc),
        )
        self.session.add(attempt)
        await self.session.flush()
        return attempt

    async def next_item(self, assessment_id, attempt_id) -> AssessmentItem | None:
        """Return next unanswered item for one-at-a-time flow."""
        stmt = (
            select(AssessmentItem)
            .where(AssessmentItem.assessment_id == assessment_id)
            .order_by(AssessmentItem.ordinal.asc())
        )
        items = list((await self.session.execute(stmt)).scalars().all())
        answered = await self.session.execute(
            select(AssessmentResponse.item_id).where(AssessmentResponse.attempt_id == attempt_id)
        )
        done = {row[0] for row in answered.all()}
        for item in items:
            if item.id not in done:
                return item
        return None

    async def submit_response(
        self,
        *,
        attempt_id,
        item_id,
        response_text: str | None = None,
        response_structured: dict | None = None,
        evaluate: bool = True,
    ) -> AssessmentResponse:
        item = await self.session.get(AssessmentItem, item_id)
        attempt = await self.session.get(AssessmentAttempt, attempt_id)
        if not item or not attempt:
            raise ValueError("not_found")

        is_correct = None
        score = None
        feedback = None
        if evaluate and item.answer_key:
            key = item.answer_key
            expected = key.get("value") or key.get("answer")
            if expected is not None and response_text is not None:
                is_correct = response_text.strip().lower() == str(expected).strip().lower()
                score = float(item.points) if is_correct else 0.0
                feedback = key.get("feedback_correct") if is_correct else key.get("feedback_incorrect")

        resp = AssessmentResponse(
            id=uuid4(),
            attempt_id=attempt_id,
            item_id=item_id,
            response_text=response_text,
            response_structured=response_structured or {},
            is_correct=is_correct,
            score=score,
            feedback=feedback,
            evidence=[{"at": datetime.now(timezone.utc).isoformat()}],
        )
        self.session.add(resp)
        await self.session.flush()
        # Objective performance evidence (not a invented mastery score)
        try:
            concept = (item.structured or {}).get("concept_key") or (
                (item.structured or {}).get("skill_key")
            )
            if not concept:
                concept = "task:" + (item.prompt or "item")[:60].lower().replace(" ", "_")
            try:
                from wax.memory.learning_state import LearningStateService
                await LearningStateService(self.session).apply_performance(
                    principal_id=attempt.principal_id,
                    concept_key=str(concept),
                    is_correct=is_correct,
                    assistance_level="independent",
                    description=(
                        f"Assessment on '{concept}': correct={is_correct}; "
                        f"answer={(response_text or '')[:120]}"
                    ),
                    source="assessment",
                    assessment_response_id=resp.id,
                )
            except Exception:
                pass
        except Exception:
            pass
        return resp

    async def complete_attempt(self, attempt_id) -> AssessmentAttempt:
        attempt = await self.session.get(AssessmentAttempt, attempt_id)
        if not attempt:
            raise ValueError("not_found")
        stmt = select(AssessmentResponse).where(AssessmentResponse.attempt_id == attempt_id)
        responses = list((await self.session.execute(stmt)).scalars().all())
        scores = [r.score for r in responses if r.score is not None]
        attempt.score = sum(scores) if scores else None
        attempt.max_score = None
        items = await self.session.execute(
            select(AssessmentItem).where(AssessmentItem.assessment_id == attempt.assessment_id)
        )
        pts = [i.points for i in items.scalars().all()]
        attempt.max_score = sum(pts) if pts else None
        attempt.status = "completed"
        attempt.completed_at = datetime.now(timezone.utc)

        assessment = await self.session.get(Assessment, attempt.assessment_id)
        if assessment:
            assessment.status = "completed"
            assessment.completed_at = attempt.completed_at
            if assessment.activity_id:
                await ActivityService(self.session).complete(
                    assessment.activity_id,
                    outcome={"attempt_id": str(attempt.id), "score": attempt.score},
                )
        await self.session.flush()
        return attempt
