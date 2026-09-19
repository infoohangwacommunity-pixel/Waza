"""
Context assembler — selective, budgeted, channel-aware.

Never dump entire history. Only what materially helps this turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import Conversation, Goal, LearningObservation, Message
from wax.delivery.presentation import platform_context_block
from wax.memory.service import MemoryService
from wax.observability.logging import get_logger

logger = get_logger(__name__)

# Soft budget in characters for assembled context (not a cost gate — quality control)
DEFAULT_CONTEXT_BUDGET = 14000


@dataclass
class AssembledContext:
    system_prefix: str
    memory_block: str
    learning_block: str
    goals_block: str
    recent_messages: list[dict[str, str]] = field(default_factory=list)
    total_chars: int = 0
    memories_used: int = 0


class ContextAssembler:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.memory = MemoryService(session)

    async def assemble(
        self,
        *,
        principal_id,
        conversation_id,
        channel: str,
        user_text: str,
        tutor_system: str,
        budget: int = DEFAULT_CONTEXT_BUDGET,
    ) -> AssembledContext:
        platform = platform_context_block(channel)
        system_prefix = tutor_system + platform

        memory_block = ""
        memories_used = 0
        if principal_id:
            mems = await self.memory.plan_and_retrieve(principal_id, user_text, limit=14)
            memories_used = len(mems)
            if mems:
                lines = [
                    f"- [{m.memory_type}|c={m.confidence:.2f}|i={m.importance:.2f}] {m.content}"
                    for m in mems
                ]
                memory_block = (
                    "\n--- Relevant memories ---\n"
                    + "\n".join(lines)
                    + "\n--- End memories ---\n"
                )
            else:
                memory_block = (
                    "\n--- Relevant memories ---\n"
                    + await self.memory.get_active_summary(principal_id, limit=12)
                    + "\n--- End memories ---\n"
                )

        learning_block = await self._learning_snapshot(principal_id)
        goals_block = await self._active_goals(principal_id)
        knowledge_block = await self._knowledge_snapshot(principal_id)
        hyp_block = await self._hypothesis_snapshot(principal_id)
        recent = await self._recent_messages(conversation_id, limit=18)
        summary_block = await self._conversation_summary(conversation_id)

        assembled = AssembledContext(
            system_prefix=system_prefix,
            memory_block=memory_block + summary_block,
            learning_block=learning_block + knowledge_block + hyp_block,
            goals_block=goals_block,
            recent_messages=recent,
            memories_used=memories_used,
        )
        assembled.total_chars = self._measure(assembled)
        while assembled.total_chars > budget and len(assembled.recent_messages) > 4:
            assembled.recent_messages.pop(0)
            assembled.total_chars = self._measure(assembled)
        if assembled.total_chars > budget and len(assembled.memory_block) > 2000:
            assembled.memory_block = assembled.memory_block[:1800] + "\n…\n--- End memories ---\n"
            assembled.total_chars = self._measure(assembled)
        return assembled

    def _measure(self, ctx: AssembledContext) -> int:
        parts = [
            ctx.system_prefix,
            ctx.memory_block,
            ctx.learning_block,
            ctx.goals_block,
        ]
        for m in ctx.recent_messages:
            parts.append(m.get("content") or "")
        return sum(len(p) for p in parts)

    async def _conversation_summary(self, conversation_id) -> str:
        if not conversation_id:
            return ""
        conv = await self.session.get(Conversation, conversation_id)
        if not conv or not conv.summary:
            return ""
        return (
            "\n--- Earlier conversation summary (long-session continuity) ---\n"
            + conv.summary[:2500]
            + "\n--- End summary ---\n"
        )

    async def _recent_messages(self, conversation_id, limit: int = 14) -> list[dict[str, str]]:
        if not conversation_id:
            return []
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        msgs = list(reversed(result.scalars().all()))
        return [{"role": m.role, "content": m.content} for m in msgs]

    async def _learning_snapshot(self, principal_id, limit: int = 8) -> str:
        if not principal_id:
            return ""
        stmt = (
            select(LearningObservation)
            .where(LearningObservation.principal_id == principal_id)
            .order_by(LearningObservation.updated_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        rows = list(result.scalars().all())
        if not rows:
            return ""
        lines = [
            f"- {r.concept_label}: mastery={r.mastery:.2f} conf={r.confidence:.2f} "
            f"attempts={r.attempts}"
            for r in rows
        ]
        return "\n--- Learning observations ---\n" + "\n".join(lines) + "\n--- End learning ---\n"

    async def _active_goals(self, principal_id, limit: int = 5) -> str:
        if not principal_id:
            return ""
        stmt = (
            select(Goal)
            .where(Goal.principal_id == principal_id, Goal.status == "active")
            .order_by(Goal.priority.desc(), Goal.updated_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        goals = list(result.scalars().all())
        if not goals:
            return ""
        lines = [f"- {g.title}" + (f": {g.description}" if g.description else "") for g in goals]
        return "\n--- Active goals ---\n" + "\n".join(lines) + "\n--- End goals ---\n"

    async def maybe_refresh_conversation_summary(
        self, conversation_id, recent: list[dict[str, str]]
    ) -> None:
        """Lightweight rolling summary for long threads — optional enrichment."""
        if not conversation_id or len(recent) < 10:
            return
        conv = await self.session.get(Conversation, conversation_id)
        if not conv:
            return
        # Store a short extractive summary without calling model every time
        tail = recent[-6:]
        bits = [f"{m['role']}: {m['content'][:120]}" for m in tail]
        conv.summary = " | ".join(bits)[:1500]
        await self.session.flush()
