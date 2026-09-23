"""
Learner Evidence Planner — decides what evidence the tutor needs for *this* decision.

Not a second tutor. Not a keyword→table map.
Deterministic code executes retrieval; planner shapes the query plan.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from wax.observability.logging import get_logger

logger = get_logger(__name__)

PLANNER_SYSTEM = """You plan evidence retrieval for a tutor that already knows the learner.
You do NOT teach. You decide what evidence would change the next teaching decision.

Return ONLY valid JSON:
{
  "decision_type": "teach|continue|schedule|correct|review|clarify|general",
  "need": ["teaching_state","goals","preferences","memory","learning","retention","knowledge","temporal","activity","prerequisites"],
  "query": "short search query for semantic retrieval",
  "concept_hints": ["optional concept labels"],
  "memory_types": ["preference","goal","learning","semantic"] or null,
  "knowledge_relevant": true/false,
  "check_corrections": true/false,
  "notes": "one line"
}

Prefer the smallest sufficient set. Prefer teaching continuity for 'continue'.
Prefer temporal+activity for reminders. Prefer knowledge when learner materials may ground the answer.
"""


@dataclass
class EvidencePlan:
    decision_type: str = "general"
    need: list[str] = field(default_factory=list)
    query: str = ""
    concept_hints: list[str] = field(default_factory=list)
    memory_types: list[str] | None = None
    knowledge_relevant: bool = False
    check_corrections: bool = False
    notes: str = ""
    planner_used: bool = False
    degraded: bool = False


@dataclass
class EvidenceItem:
    kind: str
    content: str
    relevance: str = "medium"  # high|medium|low
    confidence: float = 0.5
    source: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)
    status: str = "current"  # current|historical|uncertain|superseded

    def render(self) -> str:
        meta = f"[{self.relevance}|{self.kind}|c={self.confidence:.2f}|{self.status}]"
        src = f" src={self.source}" if self.source else ""
        return f"{meta}{src} {self.content}"


@dataclass
class EvidencePack:
    items: list[EvidenceItem] = field(default_factory=list)
    plan: EvidencePlan | None = None
    decision_hints: list[str] = field(default_factory=list)
    degraded: bool = False
    degradation_reason: str = ""

    def high_and_medium(self) -> list[EvidenceItem]:
        return [i for i in self.items if i.relevance in ("high", "medium")]


class EvidencePlanner:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def plan(self, *, user_text: str, purpose: str = "reply") -> EvidencePlan:
        """Small-model plan when available; deterministic fallback otherwise."""
        text = (user_text or "").strip()
        # Cheap deterministic path for obvious cases (no LLM required)
        low = text.lower()
        if purpose == "schedule_wake":
            return EvidencePlan(
                decision_type="schedule",
                need=["activity", "goals", "temporal", "teaching_state"],
                query=text[:200],
                knowledge_relevant=False,
                planner_used=False,
            )
        if low in ("continue", "continue.", "let's continue", "lets continue", "go on"):
            return EvidencePlan(
                decision_type="continue",
                need=["teaching_state", "learning", "goals", "activity", "memory"],
                query="continue unfinished learning thread",
                memory_types=["learning", "goal", "semantic"],
                planner_used=False,
            )
        if any(k in low for k in ("remind me", "schedule", "tomorrow at", "in an hour")):
            return EvidencePlan(
                decision_type="schedule",
                need=["temporal", "activity", "goals", "preferences"],
                query=text[:200],
                planner_used=False,
            )
        if any(k in low for k in ("not anymore", "actually i", "i'm not", "i am not", "change my", "prefer")):
            return EvidencePlan(
                decision_type="correct",
                need=["memory", "goals", "preferences"],
                query=text[:200],
                memory_types=["goal", "preference", "semantic"],
                check_corrections=True,
                planner_used=False,
            )

        # Try small/memory model planner
        try:
            from wax.intelligence.providers import ChatMessage, CompletionRequest, get_intelligence

            intel = get_intelligence()
            resp = await intel.complete(
                CompletionRequest(
                    messages=[
                        ChatMessage(role="system", content=PLANNER_SYSTEM),
                        ChatMessage(
                            role="user",
                            content=f"Purpose: {purpose}\nLearner message:\n{text[:1500]}\nPlan evidence.",
                        ),
                    ],
                    temperature=0.1,
                    max_tokens=280,
                ),
                use_memory_model=True,
            )
            data = _safe_json(resp.content or "{}")
            need = data.get("need") or ["memory", "goals", "teaching_state", "activity"]
            if not isinstance(need, list):
                need = ["memory", "goals"]
            return EvidencePlan(
                decision_type=str(data.get("decision_type") or "general"),
                need=[str(n) for n in need][:12],
                query=str(data.get("query") or text)[:300],
                concept_hints=[str(x) for x in (data.get("concept_hints") or [])][:8],
                memory_types=data.get("memory_types"),
                knowledge_relevant=bool(data.get("knowledge_relevant")),
                check_corrections=bool(data.get("check_corrections")),
                notes=str(data.get("notes") or "")[:200],
                planner_used=True,
            )
        except Exception:
            logger.info("evidence_planner_model_unavailable")
            return EvidencePlan(
                decision_type="general",
                need=["memory", "goals", "teaching_state", "activity", "learning"],
                query=text[:300],
                knowledge_relevant=True,
                degraded=True,
                planner_used=False,
            )


async def gather_evidence(
    session: AsyncSession,
    *,
    principal_id,
    user_text: str,
    purpose: str = "reply",
    conversation_id=None,
    limit: int = 24,
) -> EvidencePack:
    """Execute plan against Memory / Knowledge / LearnerModel / Retention / Temporal."""
    from wax.learner.model import build_learner_model
    from wax.memory.service import MemoryService

    planner = EvidencePlanner(session)
    plan = await planner.plan(user_text=user_text, purpose=purpose)
    pack = EvidencePack(plan=plan, degraded=plan.degraded)

    model = await build_learner_model(session, principal_id)
    need = set(plan.need or [])

    # Time always high relevance for schedule/correct/continue
    from wax.learner.temporal import now_in_tz

    local_now = now_in_tz(model.timezone)
    pack.items.append(
        EvidenceItem(
            "time",
            f"Local time {local_now.isoformat()} ({model.timezone})",
            relevance="high",
            confidence=1.0,
            source="clock",
            provenance={"timezone": model.timezone},
        )
    )

    if "activity" in need or "teaching_state" in need:
        if model.activities:
            a = model.activities[0]
            pack.items.append(
                EvidenceItem(
                    "activity",
                    f"Current activity [{a.get('status')}]: {a.get('objective') or a.get('kind')}",
                    relevance="high",
                    confidence=0.85,
                    source="activity",
                )
            )
            pack.decision_hints.append("respect_active_activity")
        for u in model.unresolved[:3]:
            pack.items.append(
                EvidenceItem(
                    "teaching",
                    f"Unresolved: {u.get('summary')} [{u.get('status')}]",
                    relevance="high",
                    confidence=0.8,
                    source="teaching",
                )
            )
        # durable teaching state if present
        try:
            from wax.learner.teaching import load_teaching_state

            ts = await load_teaching_state(session, principal_id)
            if ts:
                pack.items.append(
                    EvidenceItem(
                        "teaching",
                        ts.summary_line(),
                        relevance="high",
                        confidence=0.85,
                        source="teaching_state",
                        provenance={"thread_id": ts.thread_id},
                    )
                )
        except Exception:
            pass

    if "goals" in need:
        for g in model.goals[:4]:
            pack.items.append(
                EvidenceItem(
                    "goal",
                    f"Goal: {g.get('title')} [{g.get('status')}]",
                    relevance="high",
                    confidence=0.8,
                    source="goal",
                )
            )

    if "preferences" in need or "memory" in need:
        for p in model.preferences[:5]:
            pack.items.append(
                EvidenceItem(
                    "preference",
                    p["content"],
                    relevance="medium" if p["confidence"] < 0.7 else "high",
                    confidence=p["confidence"],
                    source="memory",
                    status="current",
                )
            )

    if "retention" in need or "learning" in need:
        for f in model.fragile[:5]:
            pack.items.append(
                EvidenceItem(
                    "retention",
                    f"Fragile: {f.get('label') or f.get('concept_key')} R={f.get('retrievability')} ({f.get('reason')})",
                    relevance="medium",
                    confidence=0.7,
                    source="retention",
                )
            )
            pack.decision_hints.append("optional_retrieval_opportunity")

    if "temporal" in need:
        for t in model.temporal_intents[:5]:
            pack.items.append(
                EvidenceItem(
                    "temporal",
                    f"Upcoming {t.get('type')}: {t.get('reason')} at {t.get('execute_at')}",
                    relevance="medium",
                    confidence=0.75,
                    source="schedule",
                )
            )

    if "memory" in need or plan.check_corrections:
        try:
            mems = await MemoryService(session).retrieve_relevant(
                principal_id,
                plan.query or user_text,
                limit=min(12, limit),
                memory_types=plan.memory_types,
            )
            for m in mems:
                pack.items.append(
                    EvidenceItem(
                        "memory",
                        m.content,
                        relevance="medium",
                        confidence=float(m.confidence or 0.5),
                        source="memory",
                        provenance={
                            "memory_id": str(m.id),
                            "type": m.memory_type,
                            "active": bool(m.is_active),
                        },
                        status="current" if m.is_active else "historical",
                    )
                )
        except Exception:
            logger.exception("evidence_memory_retrieve_failed")
            pack.degraded = True
            pack.degradation_reason = "memory_retrieve_failed"

    if plan.knowledge_relevant or "knowledge" in need:
        try:
            from wax.knowledge.ingest import KnowledgeIngestService

            hits = await KnowledgeIngestService(session).semantic_search(
                principal_id, plan.query or user_text, limit=5
            )
            for h in hits:
                pack.items.append(
                    EvidenceItem(
                        "knowledge",
                        h.get("content", "")[:700],
                        relevance="medium",
                        confidence=float(h.get("score") or 0.5),
                        source="learner_material",
                        provenance={
                            "chunk_id": h.get("chunk_id"),
                            "source_id": h.get("source_id"),
                            "label": h.get("label"),
                        },
                    )
                )
                pack.decision_hints.append("source_ground_if_using_material")
        except Exception:
            logger.exception("evidence_knowledge_retrieve_failed")

    if plan.decision_type == "continue":
        pack.decision_hints.append("reconstruct_unfinished_thread")
    if plan.check_corrections:
        pack.decision_hints.append("possible_learner_correction")

    # rank: high first, then confidence
    pack.items.sort(
        key=lambda e: (
            {"high": 0, "medium": 1, "low": 2}.get(e.relevance, 3),
            -e.confidence,
        )
    )
    pack.items = pack.items[:limit]
    logger.info(
        "evidence_gathered",
        principal_id=str(principal_id),
        decision_type=plan.decision_type,
        n=len(pack.items),
        planner_used=plan.planner_used,
        degraded=pack.degraded,
    )
    return pack


def _safe_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines)
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
    except Exception:
        pass
    return {}
