"""
Generalized prerequisite reasoning via concept graph — no subject hardcoding.

Uses ConceptRelation types: prerequisite_of, depends_on, part_of, commonly_confused_with.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import Concept, ConceptRelation, LearnerConceptState
from wax.observability.logging import get_logger

logger = get_logger(__name__)


async def find_prerequisite_gaps(
    session: AsyncSession,
    principal_id,
    concept_label: str,
    *,
    mastery_threshold: float = 0.45,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """
    Return prerequisite concepts that appear weak for this learner.
    Empty if graph has no edges or all prereqs look solid.
    """
    from wax.knowledge.graph import KnowledgeGraphService, _slug

    graph = KnowledgeGraphService(session)
    target = await graph.ensure_concept(concept_label)
    # Relations where target depends on source: from_id -prerequisite_of-> to_id
    # Convention in codebase: relate(from, to, "prerequisite_of") means from is prereq of to
    stmt = select(ConceptRelation).where(
        ConceptRelation.relation_type.in_(["prerequisite_of", "depends_on"]),
    )
    rels = list((await session.execute(stmt)).scalars().all())
    prereq_ids = []
    for r in rels:
        # prerequisite_of: from → to means from is prerequisite of to
        if r.relation_type == "prerequisite_of" and str(r.to_concept_id) == str(target.id):
            prereq_ids.append(r.from_concept_id)
        # depends_on: from depends on to → to is prerequisite of from
        if r.relation_type == "depends_on" and str(r.from_concept_id) == str(target.id):
            prereq_ids.append(r.to_concept_id)

    prereq_ids = [p for p in prereq_ids if p]
    if not prereq_ids:
        return []

    gaps = []
    for cid in prereq_ids[:20]:
        concept = await session.get(Concept, cid)
        if not concept:
            continue
        st = (
            await session.execute(
                select(LearnerConceptState).where(
                    LearnerConceptState.principal_id == principal_id,
                    LearnerConceptState.concept_id == cid,
                )
            )
        ).scalar_one_or_none()
        mastery = float(st.mastery) if st else 0.0
        if mastery < mastery_threshold:
            gaps.append(
                {
                    "concept_key": concept.key,
                    "label": concept.label,
                    "mastery": mastery,
                    "status": st.status if st else "unfamiliar",
                    "relation": "prerequisite",
                }
            )
        if len(gaps) >= limit:
            break
    logger.info(
        "prerequisite_gaps",
        principal_id=str(principal_id),
        target=concept_label,
        gaps=len(gaps),
    )
    return gaps


async def prerequisite_evidence_lines(
    session: AsyncSession, principal_id, concept_label: str
) -> list[str]:
    gaps = await find_prerequisite_gaps(session, principal_id, concept_label)
    return [
        f"Possible foundation gap: {g['label']} (mastery={g['mastery']:.2f}, {g['status']}) — repair only if it blocks understanding of {concept_label}"
        for g in gaps
    ]
