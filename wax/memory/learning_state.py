"""
Unified learning-state bridge.

Keeps LearnerConceptState, LearningObservation, and Hypothesis from drifting
apart when evidence arrives about the same claim/concept.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from wax.knowledge.graph import KnowledgeGraphService
from wax.memory.evidence import EvidenceService
from wax.observability.logging import get_logger

logger = get_logger(__name__)


class LearningStateService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.evidence = EvidenceService(session)
        self.graph = KnowledgeGraphService(session)

    async def apply_performance(
        self,
        *,
        principal_id,
        concept_key: str,
        is_correct: bool | None,
        assistance_level: str = "independent",
        description: str = "",
        source: str = "observed",
        assessment_response_id=None,
        work_id=None,
    ) -> dict[str, Any]:
        claim_key = concept_key if concept_key.startswith("concept:") else f"concept:{concept_key}"
        supports = True if is_correct is None else bool(is_correct)
        weight = 0.55
        if assistance_level in ("heavily_assisted", "answer_revealed"):
            weight = 0.25
        elif assistance_level == "independent":
            weight = 0.7

        ev = await self.evidence.record(
            principal_id=principal_id,
            evidence_type="performance" if is_correct is not None else "self_report",
            description=description or f"Performance on {claim_key}: correct={is_correct}",
            claim_key=claim_key,
            payload={
                "supports": supports,
                "is_correct": is_correct,
                "scope": "task",
            },
            assistance_level=assistance_level,
            weight=weight,
            directness=0.8,
            independence=0.85 if assistance_level == "independent" else 0.3,
            source=source,
            assessment_response_id=assessment_response_id,
            work_id=work_id,
        )

        delta = 0.0
        if is_correct is True:
            delta = 0.08 * (0.5 + 0.5 * weight)
        elif is_correct is False:
            delta = -0.06 * (0.5 + 0.5 * weight)

        label = claim_key.split(":", 1)[-1]
        state = await self.graph.update_learner_state(
            principal_id,
            label,
            mastery_delta=delta,
            status=None,
            evidence_item={
                "evidence_id": str(ev.id),
                "is_correct": is_correct,
                "assistance": assistance_level,
            },
        )
        return {
            "evidence_id": str(ev.id),
            "claim_key": claim_key,
            "mastery": getattr(state, "mastery", None),
            "confidence": getattr(state, "confidence", None),
        }
