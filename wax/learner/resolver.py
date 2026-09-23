"""
Context Resolver — ranked evidence pack for *this* decision, not a table dump.

Replaces ad-hoc multi-block assembly as the primary path while remaining
compatible with AssembledContext consumers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from wax.delivery.presentation import platform_context_block
from wax.learner.model import LearnerModel, build_learner_model
from wax.learner.temporal import now_in_tz
from wax.memory.service import MemoryService
from wax.observability.logging import get_logger

logger = get_logger(__name__)

DEFAULT_BUDGET = 14000


@dataclass
class EvidenceItem:
    relevance: str  # high|medium|low
    kind: str
    content: str
    why: str = ""
    confidence: float = 0.5
    source: str = ""

    def render(self) -> str:
        return f"[{self.relevance.upper()}|{self.kind}|c={self.confidence:.2f}] {self.content}" + (
            f" (why: {self.why})" if self.why else ""
        )


@dataclass
class LearnerContextPack:
    system_prefix: str
    evidence: list[EvidenceItem] = field(default_factory=list)
    recent_messages: list[dict[str, str]] = field(default_factory=list)
    model: LearnerModel | None = None
    total_chars: int = 0
    memories_used: int = 0
    decision_hints: list[str] = field(default_factory=list)

    def as_assembled_blocks(self) -> dict[str, Any]:
        """Compatibility shape for tutor code expecting memory/learning/goals blocks."""
        high = [e for e in self.evidence if e.relevance == "high"]
        med = [e for e in self.evidence if e.relevance == "medium"]
        mem_lines = [e.render() for e in self.evidence if e.kind in ("memory", "preference", "identity")]
        learn_lines = [e.render() for e in self.evidence if e.kind in ("learning", "retention", "misconception")]
        goal_lines = [e.render() for e in self.evidence if e.kind in ("goal", "activity", "temporal", "teaching")]
        return {
            "memory_block": ("\n--- Learner evidence (memory) ---\n" + "\n".join(mem_lines) + "\n") if mem_lines else "",
            "learning_block": ("\n--- Learning / retention ---\n" + "\n".join(learn_lines) + "\n") if learn_lines else "",
            "goals_block": ("\n--- Goals / activity / time ---\n" + "\n".join(goal_lines) + "\n") if goal_lines else "",
            "evidence_block": "\n".join(e.render() for e in high + med),
            "decision_hints": self.decision_hints,
        }


class ContextResolver:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.memory = MemoryService(session)

    async def resolve(
        self,
        *,
        principal_id,
        conversation_id,
        channel: str,
        user_text: str,
        tutor_system: str,
        budget: int = DEFAULT_BUDGET,
        purpose: str = "reply",  # reply | schedule_wake | review
    ) -> LearnerContextPack:
        platform = platform_context_block(channel)
        pack = LearnerContextPack(system_prefix=tutor_system + platform)
        if not principal_id:
            pack.recent_messages = await self._recent_messages(conversation_id)
            return pack

        model = await build_learner_model(self.session, principal_id)
        pack.model = model

        # authoritative time
        local_now = now_in_tz(model.timezone)
        pack.evidence.append(
            EvidenceItem(
                "high",
                "time",
                f"Authoritative local time: {local_now.isoformat()} ({model.timezone})",
                why="all temporal reasoning must use this",
                confidence=1.0,
                source="clock",
            )
        )

        # current activity / teaching thread
        if model.activities:
            a = model.activities[0]
            pack.evidence.append(
                EvidenceItem(
                    "high",
                    "activity",
                    f"Current activity [{a.get('status')}]: {a.get('objective') or a.get('kind')}",
                    why="what learner is doing now",
                    confidence=0.85,
                    source="activity",
                )
            )
            pack.decision_hints.append("respect_active_activity_when_interrupting")

        for g in model.goals[:4]:
            pack.evidence.append(
                EvidenceItem(
                    "high",
                    "goal",
                    f"Active goal: {g.get('title')} [{g.get('status')}]",
                    why="direction of teaching",
                    confidence=0.8,
                    source="goal",
                )
            )

        # preferences as evidence with confidence
        for p in model.preferences[:6]:
            pack.evidence.append(
                EvidenceItem(
                    "medium" if p["confidence"] < 0.7 else "high",
                    "preference",
                    p["content"],
                    why="interaction tendency — contextual, revisable",
                    confidence=p["confidence"],
                    source="memory",
                )
            )

        # fragile retention opportunities (not forced flashcards)
        for f in model.fragile[:4]:
            pack.evidence.append(
                EvidenceItem(
                    "medium",
                    "retention",
                    f"Fragile: {f.get('label') or f.get('concept_key')} R={f.get('retrievability')} ({f.get('reason')})",
                    why="retrieval opportunity candidate — tutor decides if/how",
                    confidence=0.7,
                    source="retention",
                )
            )

        # upcoming intents
        for t in model.temporal_intents[:4]:
            pack.evidence.append(
                EvidenceItem(
                    "medium",
                    "temporal",
                    f"Upcoming {t.get('type')}: {t.get('reason')} at {t.get('execute_at')}",
                    why="scheduled intention",
                    confidence=0.75,
                    source="schedule",
                )
            )

        # unresolved
        for u in model.unresolved[:3]:
            pack.evidence.append(
                EvidenceItem(
                    "high",
                    "teaching",
                    f"Unresolved: {u.get('summary')} [{u.get('status')}]",
                    why="continuity",
                    confidence=0.8,
                    source="activity",
                )
            )

        # semantic memory retrieval (existing service — still useful as sensor)
        try:
            mems = await self.memory.plan_and_retrieve(principal_id, user_text, limit=10)
            pack.memories_used = len(mems)
            for m in mems:
                pack.evidence.append(
                    EvidenceItem(
                        "medium",
                        "memory",
                        m.content,
                        why=f"type={m.memory_type}",
                        confidence=float(m.confidence or 0.5),
                        source="memory_service",
                    )
                )
        except Exception:
            logger.exception("memory_retrieve_failed")

        # continue-resolution hint
        ut = (user_text or "").strip().lower()
        if ut in ("continue", "continue.", "let's continue", "lets continue", "go on"):
            pack.decision_hints.append("user_said_continue_reconstruct_unfinished_thread")
            if model.teaching.get("current_thread"):
                pack.evidence.append(
                    EvidenceItem(
                        "high",
                        "teaching",
                        f"Likely continue target: {model.teaching['current_thread']}",
                        why="continue utterance",
                        confidence=0.9,
                        source="teaching",
                    )
                )

        pack.recent_messages = await self._recent_messages(conversation_id)
        # budget: keep high first
        ordered = sorted(
            pack.evidence,
            key=lambda e: {"high": 0, "medium": 1, "low": 2}.get(e.relevance, 3),
        )
        kept: list[EvidenceItem] = []
        size = len(pack.system_prefix)
        for e in ordered:
            line = e.render()
            if size + len(line) > budget:
                break
            kept.append(e)
            size += len(line)
        pack.evidence = kept
        pack.total_chars = size
        return pack

    async def _recent_messages(self, conversation_id, limit: int = 14) -> list[dict[str, str]]:
        if not conversation_id:
            return []
        from sqlalchemy import select
        from wax.db.models import Message

        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        rows.reverse()
        out = []
        for m in rows:
            role = "assistant" if (m.direction or "") == "outbound" else "user"
            content = (m.content or "")[:1500]
            if content:
                out.append({"role": role, "content": content})
        return out
