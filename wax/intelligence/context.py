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
from wax.domain.learner_state import build_learner_state_snapshot, format_learner_state_block
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
        # Primary path: Learner Context Resolver (connected model)
        try:
            from wax.learner.resolver import ContextResolver

            pack = await ContextResolver(self.session).resolve(
                principal_id=principal_id,
                conversation_id=conversation_id,
                channel=channel,
                user_text=user_text,
                tutor_system=tutor_system,
                budget=budget,
            )
            blocks = pack.as_assembled_blocks()
            recent = pack.recent_messages
            ctx = AssembledContext(
                system_prefix=pack.system_prefix,
                memory_block=blocks.get("memory_block") or "",
                learning_block=blocks.get("learning_block") or "",
                goals_block=blocks.get("goals_block") or "",
                recent_messages=recent,
                memories_used=pack.memories_used,
            )
            # attach hints for tutor if supported
            ctx.total_chars = pack.total_chars
            if blocks.get("decision_hints"):
                hint_txt = "\n".join(f"- {h}" for h in blocks["decision_hints"])
                ctx.goals_block = (ctx.goals_block or "") + f"\n--- Decision hints ---\n{hint_txt}\n"
            return ctx
        except Exception:
            logger.exception("context_resolver_failed_explicit_degraded")
            # Explicit degraded mode — still assemble minimal context; observability marks failure
            logger.warning("learner_context_degraded", reason="resolver_exception")
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
        prefs_block = await self._preferences_block(principal_id)
        checkin_block = await self._checkin_eligibility_block(principal_id, conversation_id)
        situation_block = ""
        if principal_id:
            try:
                snap = await build_learner_state_snapshot(self.session, principal_id)
                situation_block = format_learner_state_block(snap)
            except Exception:
                logger.exception("learner_state_snapshot_failed")
        knowledge_block = await self._knowledge_snapshot(principal_id)
        materials_block = await self._learner_materials_block(principal_id, user_text)
        hyp_block = await self._hypothesis_snapshot(principal_id)
        recent = await self._recent_messages(conversation_id, limit=18)
        summary_block = await self._conversation_summary(conversation_id)

        assembled = AssembledContext(
            system_prefix=system_prefix,
            memory_block=memory_block + summary_block + prefs_block + checkin_block,
            learning_block=learning_block + knowledge_block + materials_block + hyp_block,
            goals_block=goals_block + situation_block,
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


    async def _checkin_eligibility_block(self, principal_id, conversation_id) -> str:
        """Infrastructure gate only — AI still decides if the moment is natural."""
        if not principal_id:
            return ""
        try:
            from wax.learner.checkin import checkin_allowed

            allowed, reason = await checkin_allowed(
                self.session,
                principal_id=principal_id,
                conversation_id=conversation_id,
            )
            if allowed:
                return (
                    "\n--- Check-in eligibility ---\n"
                    "Infrastructure permits an occasional natural check-in if the conversation "
                    "has drifted to casual reflection (not during active problem-solving). "
                    "Do not survey. Do not force ratings. Only ask if it feels natural.\n"
                    "--- End check-in ---\n"
                )
            return ""
        except Exception:
            logger.exception("checkin_eligibility_failed")
            return ""

    async def _preferences_block(self, principal_id) -> str:
        """Surface durable explicit preferences so the tutor does not drift."""
        if not principal_id:
            return ""
        try:
            from wax.domain.preferences import get_preferences

            prefs = await get_preferences(self.session, principal_id)
        except Exception:
            logger.exception("preferences_block_failed")
            return ""
        # Only show non-default / meaningful keys
        interesting = []
        for key in (
            "message_length",
            "language",
            "timezone",
            "emoji",
            "tone",
            "learning_style",
            "quiet_hours",
        ):
            val = prefs.get(key)
            if val is None or val == "" or val is False:
                continue
            interesting.append(f"- {key}: {val}")
        # Any extra custom keys the learner set
        for key, val in (prefs or {}).items():
            if key in (
                "message_length",
                "language",
                "timezone",
                "emoji",
                "tone",
                "learning_style",
                "quiet_hours",
            ):
                continue
            if val is None or val == "":
                continue
            interesting.append(f"- {key}: {val}")
        if not interesting:
            return ""
        return (
            "\n--- Durable preferences (honor unless current turn overrides) ---\n"
            + "\n".join(interesting)
            + "\n--- End preferences ---\n"
        )

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



    async def _learner_materials_block(self, principal_id, user_text: str = "") -> str:
        if not principal_id:
            return ""
        try:
            from wax.knowledge.ingest import KnowledgeIngestService

            svc = KnowledgeIngestService(self.session)
            sources = await svc.list_sources(principal_id, limit=8)
            chunks = await svc.recent_chunks_for_context(principal_id, query=user_text, limit=4)
        except Exception:
            logger.exception("learner_materials_block_failed")
            return ""
        if not sources and not chunks:
            return ""
        lines = []
        if sources:
            lines.append("Sources:")
            for s in sources[:8]:
                lines.append(f"- {s['title']} ({s['kind']}/{s['status']})")
        if chunks:
            lines.append("Relevant extracts:")
            for c in chunks:
                lines.append(f"- {c[:400]}")
        return (
            "\n--- Learner materials (this person only) ---\n"
            + "\n".join(lines)
            + "\n--- End materials ---\n"
        )

    async def _knowledge_snapshot(self, principal_id, limit: int = 10) -> str:
        if not principal_id:
            return ""
        try:
            from wax.knowledge.graph import KnowledgeGraphService
            snap = await KnowledgeGraphService(self.session).learner_snapshot(
                principal_id, limit=limit
            )
        except Exception:
            return ""
        if not snap:
            return ""
        lines = [
            f"- {s['concept']}: status={s['status']} mastery={s['mastery']:.2f} conf={s['confidence']:.2f}"
            for s in snap
        ]
        return (
            "\n--- Concept understanding (personalized graph) ---\n"
            + "\n".join(lines)
            + "\n--- End concepts ---\n"
        )

    async def _hypothesis_snapshot(self, principal_id, limit: int = 8) -> str:
        if not principal_id:
            return ""
        try:
            from wax.memory.evidence import EvidenceService
            hyps = await EvidenceService(self.session).active_hypotheses(
                principal_id, limit=limit
            )
        except Exception:
            return ""
        if not hyps:
            return ""
        lines = [
            f"- [{h['status']}] conf={h['confidence']:.2f}: {h['claim'][:160]}"
            for h in hyps
        ]
        research_hint = ""
        try:
            from wax.memory.research_loop import ResearchLoopService
            prop = await ResearchLoopService(self.session).propose_next_test(principal_id)
            if prop:
                research_hint = (
                    f"\nOptional next check (do not force): {prop.get('suggestion', '')[:240]}\n"
                )
        except Exception:
            pass
        return (
            "\n--- Current hypotheses about this learner (not facts) ---\n"
            + "\n".join(lines)
            + research_hint
            + "\nTreat as tentative. Prefer evidence over assumption.\n--- End hypotheses ---\n"
        )

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
