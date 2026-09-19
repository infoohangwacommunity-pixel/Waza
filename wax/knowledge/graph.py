"""
Personalized knowledge graph — concepts + relations + learner state.

No Subject→Chapter→Topic hierarchy hardcoded.
Relations are open vocabulary; intelligence decides meaning.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import Concept, ConceptRelation, LearnerConceptState
from wax.observability.logging import get_logger

logger = get_logger(__name__)


def _slug(text: str) -> str:
    s = "".join(c.lower() if c.isalnum() else "-" for c in (text or "").strip())
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-")[:200] or f"concept-{uuid4().hex[:8]}"


class KnowledgeGraphService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def ensure_concept(
        self, label: str, *, domain_key: str | None = None, description: str | None = None
    ) -> Concept:
        key = _slug(label)
        stmt = select(Concept).where(Concept.key == key)
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            return existing
        c = Concept(
            id=uuid4(),
            key=key,
            label=label[:500],
            description=description,
            domain_key=domain_key,
        )
        self.session.add(c)
        await self.session.flush()
        return c

    async def relate(
        self,
        from_label: str,
        to_label: str,
        relation_type: str,
        *,
        strength: float = 0.5,
    ) -> ConceptRelation:
        a = await self.ensure_concept(from_label)
        b = await self.ensure_concept(to_label)
        rel = ConceptRelation(
            id=uuid4(),
            from_concept_id=a.id,
            to_concept_id=b.id,
            relation_type=relation_type[:80],
            strength=strength,
            evidence=[{"at": datetime.now(timezone.utc).isoformat()}],
        )
        self.session.add(rel)
        await self.session.flush()
        return rel

    async def update_learner_state(
        self,
        principal_id,
        concept_label: str,
        *,
        mastery_delta: float = 0.0,
        status: str | None = None,
        note: str | None = None,
        evidence_item: dict[str, Any] | None = None,
    ) -> LearnerConceptState:
        concept = await self.ensure_concept(concept_label)
        stmt = select(LearnerConceptState).where(
            LearnerConceptState.principal_id == principal_id,
            LearnerConceptState.concept_id == concept.id,
        )
        result = await self.session.execute(stmt)
        state = result.scalar_one_or_none()
        if not state:
            state = LearnerConceptState(
                id=uuid4(),
                principal_id=principal_id,
                concept_id=concept.id,
                status=status or "unfamiliar",
                mastery=max(0.0, min(1.0, 0.2 + mastery_delta)),
                confidence=0.3,
                evidence=[],
            )
            self.session.add(state)
        else:
            state.mastery = max(0.0, min(1.0, state.mastery + mastery_delta))
            state.confidence = min(1.0, state.confidence + 0.05)
            if status:
                state.status = status
            if evidence_item:
                state.evidence = list(state.evidence or []) + [evidence_item]
            if note:
                state.notes = note
        state.last_observed_at = datetime.now(timezone.utc)
        await self.session.flush()
        return state

    async def learner_snapshot(self, principal_id, limit: int = 12) -> list[dict[str, Any]]:
        stmt = (
            select(LearnerConceptState, Concept)
            .join(Concept, Concept.id == LearnerConceptState.concept_id)
            .where(LearnerConceptState.principal_id == principal_id)
            .order_by(LearnerConceptState.updated_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        out = []
        for state, concept in result.all():
            out.append(
                {
                    "concept": concept.label,
                    "status": state.status,
                    "mastery": state.mastery,
                    "confidence": state.confidence,
                }
            )
        return out
