"""
Evidence pipeline — observable basis for claims, not AI-invented scores.

Hierarchy:
  Event (always) → Observation (selective) → Evidence (meaningful)
  → Hypothesis (cautious) → Learner state / Durable memory (justified)

Never promote model guesses to facts without evidence.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import Evidence, Hypothesis, Memory, Observation
from wax.observability.logging import get_logger

logger = get_logger(__name__)

# Infrastructure taxonomy — not educational subjects
EVIDENCE_TYPES = {
    "explicit",
    "performance",
    "explanation",
    "demonstration",
    "correction",
    "repetition",
    "persistence",
    "transfer",
    "self_report",
    "tutor_intervention",
    "independent_success",
    "behavioral",
}

ASSISTANCE_LEVELS = {
    "independent",
    "hint",
    "guided",
    "partially_solved",
    "heavily_assisted",
    "answer_revealed",
    "unknown",
}

HYPOTHESIS_STATUSES = {
    "candidate",
    "active",
    "confirmed",
    "superseded",
    "contradicted",
    "expired",
    "forgotten",
}


class EvidenceService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def record(
        self,
        *,
        principal_id,
        evidence_type: str,
        description: str,
        claim_key: str | None = None,
        payload: dict[str, Any] | None = None,
        assistance_level: str = "unknown",
        weight: float = 0.5,
        directness: float = 0.5,
        independence: float = 0.5,
        specificity: float = 0.5,
        source: str = "observed",
        observation_id=None,
        message_id=None,
        work_id=None,
        assessment_response_id=None,
    ) -> Evidence:
        et = evidence_type if evidence_type in EVIDENCE_TYPES else "performance"
        al = assistance_level if assistance_level in ASSISTANCE_LEVELS else "unknown"
        # Independence from assistance
        if al == "independent":
            independence = max(independence, 0.85)
        elif al in ("heavily_assisted", "answer_revealed"):
            independence = min(independence, 0.2)

        ev = Evidence(
            id=uuid4(),
            principal_id=principal_id,
            evidence_type=et,
            claim_key=claim_key,
            description=description[:4000],
            payload=payload or {},
            assistance_level=al,
            weight=max(0.0, min(1.0, weight)),
            directness=directness,
            independence=independence,
            specificity=specificity,
            source=source,
            observation_id=observation_id,
            message_id=message_id,
            work_id=work_id,
            assessment_response_id=assessment_response_id,
        )
        self.session.add(ev)
        await self.session.flush()

        if claim_key:
            await self._update_hypothesis_from_evidence(principal_id, claim_key, ev)
        return ev

    async def _update_hypothesis_from_evidence(
        self, principal_id, claim_key: str, evidence: Evidence
    ) -> Hypothesis | None:
        stmt = (
            select(Hypothesis)
            .where(
                Hypothesis.principal_id == principal_id,
                Hypothesis.claim_key == claim_key,
                Hypothesis.status.in_(["candidate", "active", "confirmed"]),
            )
            .order_by(Hypothesis.updated_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        hyp = result.scalar_one_or_none()
        now = datetime.now(timezone.utc)

        # Direction of evidence from payload if present
        supports = evidence.payload.get("supports", True)
        delta = self._confidence_delta(evidence, supports=supports)

        if not hyp:
            hyp = Hypothesis(
                id=uuid4(),
                principal_id=principal_id,
                claim_key=claim_key,
                claim=evidence.description[:1000],
                status="candidate",
                confidence=max(0.1, min(0.9, 0.3 + delta)),
                rationale=f"Formed from {evidence.evidence_type} evidence",
                supporting_evidence_ids=[str(evidence.id)] if supports else [],
                contradicting_evidence_ids=[] if supports else [str(evidence.id)],
                first_formed_at=now,
                last_updated_at=now,
            )
            self.session.add(hyp)
            await self.session.flush()
            return hyp

        if supports:
            hyp.supporting_evidence_ids = list(hyp.supporting_evidence_ids or []) + [
                str(evidence.id)
            ]
        else:
            hyp.contradicting_evidence_ids = list(hyp.contradicting_evidence_ids or []) + [
                str(evidence.id)
            ]
        hyp.confidence = self._apply_odds_update(hyp.confidence, evidence, supports)

        hyp.last_updated_at = now
        # Lifecycle transitions
        support_n = len(hyp.supporting_evidence_ids or [])
        contra_n = len(hyp.contradicting_evidence_ids or [])
        if hyp.status == "candidate" and support_n >= 2 and hyp.confidence >= 0.45:
            hyp.status = "active"
        if hyp.status in ("candidate", "active") and support_n >= 4 and hyp.confidence >= 0.75:
            hyp.status = "confirmed"
            hyp.confirmed_at = now
            await self._maybe_promote_to_memory(hyp)
        if contra_n >= 2 and hyp.confidence < 0.35:
            hyp.status = "contradicted"
        if hyp.status == "confirmed" and contra_n >= 3 and hyp.confidence < 0.4:
            hyp.status = "contradicted"

        hyp.rationale = self._build_rationale(hyp)
        await self.session.flush()
        return hyp

    def _confidence_delta(self, evidence: Evidence, supports: bool = True) -> float:
        """Likelihood-ratio style step toward Bayesian update (still a heuristic)."""
        # Likelihood ratio strength from quality signals
        lr = 1.0 + 0.35 * evidence.weight
        lr *= 0.6 + 0.4 * evidence.directness
        lr *= 0.6 + 0.4 * evidence.independence
        lr *= 0.6 + 0.4 * evidence.specificity
        type_mult = {
            "explicit": 1.3,
            "independent_success": 1.45,
            "transfer": 1.55,
            "persistence": 1.25,
            "demonstration": 1.35,
            "self_report": 0.75,
            "tutor_intervention": 0.55,
            "performance": 1.0,
        }.get(evidence.evidence_type, 1.0)
        lr *= type_mult
        if evidence.assistance_level in ("heavily_assisted", "answer_revealed"):
            lr = 1.0 + (lr - 1.0) * 0.35
        # Convert LR to additive delta on probability scale around mid confidence
        # delta ≈ (lr-1)/(lr+1) * scale
        strength = (lr - 1.0) / (lr + 1.0)
        delta = 0.18 * strength
        return delta if supports else -delta

    def _apply_odds_update(self, prior: float, evidence: Evidence, supports: bool) -> float:
        """Odds-form Bayesian update: posterior = normalize(prior_odds * LR)."""
        p = max(0.02, min(0.98, prior))
        odds = p / (1.0 - p)
        lr = 1.0 + abs(self._confidence_delta(evidence, supports=True)) * 8.0
        if not supports:
            lr = 1.0 / max(lr, 1.01)
        new_odds = odds * lr
        return max(0.02, min(0.98, new_odds / (1.0 + new_odds)))

    def _build_rationale(self, hyp: Hypothesis) -> str:
        s = len(hyp.supporting_evidence_ids or [])
        c = len(hyp.contradicting_evidence_ids or [])
        return (
            f"status={hyp.status}; confidence={hyp.confidence:.2f}; "
            f"supporting={s}; contradicting={c}"
        )

    async def _maybe_promote_to_memory(self, hyp: Hypothesis) -> Memory | None:
        """Only confirmed hypotheses with enough evidence become durable memories."""
        if hyp.status != "confirmed" or hyp.memory_id:
            return None
        if len(hyp.supporting_evidence_ids or []) < 3:
            return None
        mem = Memory(
            id=uuid4(),
            principal_id=hyp.principal_id,
            memory_type="learning",
            content=hyp.claim,
            structured={
                "claim_key": hyp.claim_key,
                "hypothesis_id": str(hyp.id),
                "kind": "promoted_hypothesis",
            },
            confidence=hyp.confidence,
            importance=min(0.9, 0.5 + hyp.confidence * 0.4),
            source="system_derived",
            evidence=[
                {"hypothesis_id": str(hyp.id), "supporting": hyp.supporting_evidence_ids}
            ],
            is_active=True,
            validity_status="active",
            tags=["hypothesis_promoted", hyp.claim_key.split(":")[0] if ":" in hyp.claim_key else "learning"],
        )
        self.session.add(mem)
        await self.session.flush()
        hyp.memory_id = mem.id
        await self.session.flush()
        # Unify with concept state when claim_key looks like a concept
        if hyp.claim_key.startswith("concept:"):
            try:
                from wax.knowledge.graph import KnowledgeGraphService
                label = hyp.claim_key.split(":", 1)[1]
                await KnowledgeGraphService(self.session).update_learner_state(
                    hyp.principal_id,
                    label,
                    mastery_delta=0.05,
                    status="partial" if hyp.confidence < 0.7 else "applied",
                    evidence_item={
                        "source": "confirmed_hypothesis",
                        "hypothesis_id": str(hyp.id),
                        "confidence": hyp.confidence,
                    },
                )
            except Exception:
                pass
        logger.info("hypothesis_promoted_to_memory", hypothesis_id=str(hyp.id))
        return mem

    async def form_hypothesis(
        self,
        *,
        principal_id,
        claim_key: str,
        claim: str,
        initial_confidence: float = 0.3,
        rationale: str | None = None,
    ) -> Hypothesis:
        """Explicit hypothesis formation — status starts as candidate, never fact."""
        now = datetime.now(timezone.utc)
        stmt = select(Hypothesis).where(
            Hypothesis.principal_id == principal_id,
            Hypothesis.claim_key == claim_key,
            Hypothesis.status.in_(["candidate", "active", "confirmed"]),
        )
        existing = (await self.session.execute(stmt)).scalar_one_or_none()
        if existing:
            return existing
        hyp = Hypothesis(
            id=uuid4(),
            principal_id=principal_id,
            claim_key=claim_key,
            claim=claim[:2000],
            status="candidate",
            confidence=initial_confidence,
            rationale=rationale or "Candidate hypothesis — not yet confirmed",
            first_formed_at=now,
            last_updated_at=now,
        )
        self.session.add(hyp)
        await self.session.flush()
        return hyp

    async def why_we_believe(self, principal_id, claim_key: str) -> dict[str, Any]:
        """Answer: WHAT + WHY + WHEN + HOW WE KNOW."""
        stmt = (
            select(Hypothesis)
            .where(
                Hypothesis.principal_id == principal_id,
                Hypothesis.claim_key == claim_key,
            )
            .order_by(Hypothesis.updated_at.desc())
            .limit(1)
        )
        hyp = (await self.session.execute(stmt)).scalar_one_or_none()
        if not hyp:
            return {"found": False, "claim_key": claim_key}

        evidence_ids = list(hyp.supporting_evidence_ids or []) + list(
            hyp.contradicting_evidence_ids or []
        )
        evidence_rows = []
        for eid in evidence_ids[:20]:
            try:
                from uuid import UUID

                row = await self.session.get(Evidence, UUID(str(eid)))
            except Exception:
                row = None
            if row:
                evidence_rows.append(
                    {
                        "id": str(row.id),
                        "type": row.evidence_type,
                        "description": row.description[:300],
                        "assistance": row.assistance_level,
                        "weight": row.weight,
                        "created_at": row.created_at.isoformat() if row.created_at else None,
                        "supports": str(row.id) in (hyp.supporting_evidence_ids or []),
                    }
                )
        return {
            "found": True,
            "claim": hyp.claim,
            "claim_key": hyp.claim_key,
            "status": hyp.status,
            "confidence": hyp.confidence,
            "rationale": hyp.rationale,
            "evidence": evidence_rows,
            "confirmed_at": hyp.confirmed_at.isoformat() if hyp.confirmed_at else None,
        }

    async def active_hypotheses(self, principal_id, limit: int = 12) -> list[dict[str, Any]]:
        stmt = (
            select(Hypothesis)
            .where(
                Hypothesis.principal_id == principal_id,
                Hypothesis.status.in_(["candidate", "active", "confirmed"]),
            )
            .order_by(Hypothesis.confidence.desc())
            .limit(limit)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        return [
            {
                "claim_key": h.claim_key,
                "claim": h.claim,
                "status": h.status,
                "confidence": h.confidence,
                "rationale": h.rationale,
            }
            for h in rows
        ]
