"""
Context Resolver — decision-oriented evidence pack via Evidence Planner.

No silent regression to obsolete architecture without observability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from wax.delivery.presentation import platform_context_block
from wax.learner.evidence import EvidencePack, gather_evidence
from wax.learner.model import LearnerModel
from wax.observability.logging import get_logger

logger = get_logger(__name__)

DEFAULT_BUDGET = 14000


@dataclass
class LearnerContextPack:
    system_prefix: str
    evidence: list = field(default_factory=list)
    recent_messages: list[dict[str, str]] = field(default_factory=list)
    model: LearnerModel | None = None
    total_chars: int = 0
    memories_used: int = 0
    decision_hints: list[str] = field(default_factory=list)
    degraded: bool = False
    degradation_reason: str = ""
    pack: EvidencePack | None = None

    def as_assembled_blocks(self) -> dict[str, Any]:
        items = self.evidence
        mem_lines = [e.render() for e in items if getattr(e, "kind", "") in ("memory", "preference", "identity")]
        learn_lines = [e.render() for e in items if getattr(e, "kind", "") in ("learning", "retention", "misconception", "teaching")]
        goal_lines = [e.render() for e in items if getattr(e, "kind", "") in ("goal", "activity", "temporal", "time", "knowledge")]
        return {
            "memory_block": ("\n--- Learner evidence (memory) ---\n" + "\n".join(mem_lines) + "\n") if mem_lines else "",
            "learning_block": ("\n--- Learning / teaching / retention ---\n" + "\n".join(learn_lines) + "\n") if learn_lines else "",
            "goals_block": ("\n--- Goals / activity / time / materials ---\n" + "\n".join(goal_lines) + "\n") if goal_lines else "",
            "evidence_block": "\n".join(e.render() for e in items if getattr(e, "relevance", "") in ("high", "medium")),
            "decision_hints": self.decision_hints,
            "degraded": self.degraded,
            "degradation_reason": self.degradation_reason,
        }


class ContextResolver:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def resolve(
        self,
        *,
        principal_id,
        conversation_id,
        channel: str,
        user_text: str,
        tutor_system: str,
        budget: int = DEFAULT_BUDGET,
        purpose: str = "reply",
    ) -> LearnerContextPack:
        platform = platform_context_block(channel)
        identity_txt = ""
        continuity_txt = ""
        if principal_id:
            try:
                from wax.domain.identity import (
                    linked_channels_state,
                    format_linked_channels_block,
                    recent_cross_channel_snippets,
                    format_cross_channel_continuity_block,
                )

                state = await linked_channels_state(
                    self.session, principal_id=principal_id, current_channel=channel
                )
                identity_txt = format_linked_channels_block(state)
                # Only fetch other-channel snippets when more than one permanent channel is linked
                linked = state.get("linked_channels") or []
                if len(linked) > 1:
                    snippets = await recent_cross_channel_snippets(
                        self.session,
                        principal_id=principal_id,
                        current_channel=channel,
                    )
                    continuity_txt = format_cross_channel_continuity_block(snippets)
            except Exception:
                logger.exception("linked_channels_state_failed")
        pack = LearnerContextPack(system_prefix=tutor_system + platform + identity_txt + continuity_txt)
        if not principal_id:
            pack.recent_messages = await self._recent_messages(conversation_id)
            pack.degraded = True
            pack.degradation_reason = "no_principal"
            return pack

        # Context Intelligence: model-guided investigation (falls back to bounded probe)
        try:
            from wax.intelligence.context_intel import investigate_context, brief_to_tutor_text

            brief = await investigate_context(
                self.session,
                principal_id=principal_id,
                conversation_id=conversation_id,
                channel=channel,
                user_text=user_text,
            )
            brief_txt = brief_to_tutor_text(brief)
            if brief_txt:
                pack.system_prefix = pack.system_prefix + brief_txt
            if brief.tools_used:
                pack.decision_hints.append(
                    "context_intel_tools:" + ",".join(brief.tools_used[:8])
                )
            if brief.no_context_required:
                pack.decision_hints.append("context_intel:no_context_required")
            if brief.insufficient_evidence:
                pack.decision_hints.append("context_intel:insufficient_evidence")
            if brief.degraded:
                pack.decision_hints.append(
                    f"context_intel_degraded:{brief.degradation_reason or 'partial'}"
                )
        except Exception:
            logger.exception("context_intelligence_integration_failed")

        evidence = await gather_evidence(
            self.session,
            principal_id=principal_id,
            user_text=user_text,
            purpose=purpose,
            conversation_id=conversation_id,
            limit=28,
        )
        pack.pack = evidence
        pack.evidence = evidence.items
        pack.decision_hints = list(evidence.decision_hints)
        pack.degraded = evidence.degraded
        pack.degradation_reason = evidence.degradation_reason
        pack.memories_used = sum(1 for i in evidence.items if i.kind == "memory")
        if evidence.degraded:
            logger.warning(
                "learner_context_degraded",
                principal_id=str(principal_id),
                reason=evidence.degradation_reason,
            )
            pack.decision_hints.append(f"degraded:{evidence.degradation_reason or 'partial'}")

        # budget trim
        size = len(pack.system_prefix)
        kept = []
        for e in evidence.items:
            line = e.render()
            if size + len(line) > budget:
                break
            kept.append(e)
            size += len(line)
        pack.evidence = kept
        pack.total_chars = size
        pack.recent_messages = await self._recent_messages(conversation_id)

        if evidence.plan and evidence.plan.check_corrections:
            try:
                from wax.learner.correction import apply_possible_correction

                corr = await apply_possible_correction(
                    self.session, principal_id=principal_id, user_text=user_text
                )
                if corr.get("corrected"):
                    pack.decision_hints.append("learner_correction_applied")
                    for d in corr.get("details") or []:
                        pack.evidence.insert(
                            0,
                            type(evidence.items[0])(
                                kind="memory",
                                content=f"CORRECTED ({d.get('type')}): {d.get('old_content')} → {d.get('new_content')}",
                                relevance="high",
                                confidence=0.9,
                                source="correction",
                                status="current",
                            ) if evidence.items else None,
                        )
                    pack.evidence = [e for e in pack.evidence if e]
            except Exception:
                logger.exception("correction_apply_failed")

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
