"""
Domain capabilities for Context Intelligence.

Principal isolation is enforced here — the model cannot request another learner.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.observability.logging import get_logger

logger = get_logger(__name__)

# Tool specs for the orchestration model (OpenAI-compatible function schema)
CAPABILITY_SPECS: list[dict[str, Any]] = [
    {
        "name": "inspect_learner_state",
        "description": (
            "Authoritative snapshot of the current learner's situation (activity, goals, "
            "recent focus). Use when personalization may matter. Returns facts only."
        ),
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "search_memories",
        "description": (
            "Search durable memories for this learner only. Pass a short query about what "
            "might matter for the current request. Returns candidate memories with confidence."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for"},
                "limit": {"type": "integer", "description": "Max items (default 8, max 14)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "inspect_recent_conversation",
        "description": (
            "Recent messages in the current conversation. Use for continuity "
            "('still don't get it', 'continue', pronouns referring to prior turns)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max messages (default 10, max 20)"},
            },
        },
    },
    {
        "name": "inspect_goals",
        "description": "Active goals for this learner. Use for planning or long-horizon requests.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "inspect_preferences",
        "description": "Stored teaching/interaction preferences for this learner, if any.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "search_evidence",
        "description": (
            "Gather decision-oriented learning evidence for this learner relative to the "
            "current user text (observations, teaching signals, retention). Principal-scoped."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "purpose": {
                    "type": "string",
                    "description": "reply | teach | plan | continue (default reply)",
                },
            },
        },
    },
    {
        "name": "inspect_linked_channels",
        "description": "Authoritative messaging identity state (WhatsApp/Telegram linked or not).",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "inspect_hypotheses",
        "description": "Active hypotheses about the learner (not facts).",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


class ContextCapabilities:
    def __init__(
        self,
        session: AsyncSession,
        *,
        principal_id: UUID,
        conversation_id: UUID | None = None,
        channel: str = "",
        user_text: str = "",
    ):
        self.session = session
        self.principal_id = principal_id
        self.conversation_id = conversation_id
        self.channel = channel
        self.user_text = user_text

    async def execute(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        args = args or {}
        try:
            if name == "inspect_learner_state":
                return await self._learner_state()
            if name == "search_memories":
                return await self._search_memories(
                    str(args.get("query") or self.user_text or ""),
                    int(args.get("limit") or 8),
                )
            if name == "inspect_recent_conversation":
                return await self._recent_conversation(int(args.get("limit") or 10))
            if name == "inspect_goals":
                return await self._goals()
            if name == "inspect_preferences":
                return await self._preferences()
            if name == "search_evidence":
                return await self._search_evidence(str(args.get("purpose") or "reply"))
            if name == "inspect_linked_channels":
                return await self._linked_channels()
            if name == "inspect_hypotheses":
                return await self._hypotheses()
            return {"ok": False, "error": f"unknown_capability:{name}"}
        except Exception as e:
            logger.exception("context_capability_failed", capability=name)
            return {"ok": False, "error": "capability_failed", "detail": str(e)[:200]}

    async def _learner_state(self) -> dict[str, Any]:
        try:
            from wax.domain.learner_state import build_learner_state_snapshot

            snap = await build_learner_state_snapshot(self.session, self.principal_id)
            if hasattr(snap, "__dict__"):
                data = {k: v for k, v in vars(snap).items() if not k.startswith("_")}
            elif isinstance(snap, dict):
                data = snap
            else:
                data = {"snapshot": str(snap)[:500]}
            return {"ok": True, "kind": "fact", "source": "learner_state", "data": data}
        except Exception:
            logger.exception("inspect_learner_state_failed")
            return {"ok": False, "error": "learner_state_unavailable"}

    async def _search_memories(self, query: str, limit: int) -> dict[str, Any]:
        limit = max(1, min(14, limit))
        from wax.memory.service import MemoryService

        mems = await MemoryService(self.session).plan_and_retrieve(
            self.principal_id, query or self.user_text or " ", limit=limit
        )
        items = [
            {
                "content": (m.content or "")[:400],
                "memory_type": getattr(m, "memory_type", ""),
                "confidence": float(getattr(m, "confidence", 0) or 0),
                "importance": float(getattr(m, "importance", 0) or 0),
            }
            for m in mems
        ]
        return {
            "ok": True,
            "kind": "evidence",
            "source": "memory",
            "count": len(items),
            "items": items,
        }

    async def _recent_conversation(self, limit: int) -> dict[str, Any]:
        limit = max(1, min(20, limit))
        if not self.conversation_id:
            return {"ok": True, "kind": "fact", "source": "conversation", "items": []}
        from wax.db.models import Message

        rows = list(
            (
                await self.session.execute(
                    select(Message)
                    .where(Message.conversation_id == self.conversation_id)
                    .order_by(Message.created_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        items = [
            {
                "role": m.role or "user",
                "content": (m.content or "")[:300],
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in reversed(rows)
        ]
        return {
            "ok": True,
            "kind": "fact",
            "source": "conversation",
            "count": len(items),
            "items": items,
        }

    async def _goals(self) -> dict[str, Any]:
        from wax.db.models import Goal

        rows = list(
            (
                await self.session.execute(
                    select(Goal)
                    .where(Goal.principal_id == self.principal_id, Goal.status == "active")
                    .order_by(Goal.updated_at.desc())
                    .limit(8)
                )
            )
            .scalars()
            .all()
        )
        items = [
            {
                "title": getattr(g, "title", None) or getattr(g, "content", "") or str(g.id),
                "status": getattr(g, "status", ""),
            }
            for g in rows
        ]
        return {"ok": True, "kind": "fact", "source": "goals", "items": items}

    async def _preferences(self) -> dict[str, Any]:
        try:
            from wax.domain.preferences import get_preference_summary

            summary = await get_preference_summary(self.session, self.principal_id)
            return {
                "ok": True,
                "kind": "evidence",
                "source": "preferences",
                "summary": summary if isinstance(summary, (str, dict, list)) else str(summary)[:800],
            }
        except Exception:
            # Soft fail — preferences module may vary
            from wax.db.models import Memory

            rows = list(
                (
                    await self.session.execute(
                        select(Memory)
                        .where(
                            Memory.principal_id == self.principal_id,
                            Memory.memory_type == "preference",
                            Memory.is_active.is_(True),
                        )
                        .order_by(Memory.updated_at.desc())
                        .limit(8)
                    )
                )
                .scalars()
                .all()
            )
            return {
                "ok": True,
                "kind": "evidence",
                "source": "preferences",
                "items": [{"content": (m.content or "")[:300]} for m in rows],
            }

    async def _search_evidence(self, purpose: str) -> dict[str, Any]:
        from wax.learner.evidence import gather_evidence

        pack = await gather_evidence(
            self.session,
            principal_id=self.principal_id,
            user_text=self.user_text or " ",
            purpose=purpose or "reply",
            conversation_id=self.conversation_id,
            limit=16,
        )
        items = []
        for e in getattr(pack, "items", []) or []:
            items.append(
                {
                    "kind": getattr(e, "kind", ""),
                    "text": (e.render() if hasattr(e, "render") else str(e))[:400],
                    "relevance": getattr(e, "relevance", ""),
                }
            )
        return {
            "ok": True,
            "kind": "evidence",
            "source": "evidence_planner",
            "count": len(items),
            "items": items,
            "hints": list(getattr(pack, "decision_hints", []) or [])[:8],
            "degraded": bool(getattr(pack, "degraded", False)),
        }

    async def _linked_channels(self) -> dict[str, Any]:
        from wax.domain.identity import linked_channels_state

        state = await linked_channels_state(
            self.session, principal_id=self.principal_id, current_channel=self.channel
        )
        return {"ok": True, "kind": "fact", "source": "identity", "data": state}

    async def _hypotheses(self) -> dict[str, Any]:
        try:
            from wax.db.models import Hypothesis

            rows = list(
                (
                    await self.session.execute(
                        select(Hypothesis)
                        .where(
                            Hypothesis.principal_id == self.principal_id,
                            Hypothesis.status.in_(["active", "candidate"]),
                        )
                        .order_by(Hypothesis.updated_at.desc())
                        .limit(8)
                    )
                )
                .scalars()
                .all()
            )
            items = [
                {
                    "claim": (getattr(h, "claim", None) or getattr(h, "description", "") or "")[:300],
                    "status": getattr(h, "status", ""),
                    "confidence": float(getattr(h, "confidence", 0) or 0),
                }
                for h in rows
            ]
            return {"ok": True, "kind": "hypothesis", "source": "hypothesis", "items": items}
        except Exception:
            return {"ok": True, "kind": "hypothesis", "source": "hypothesis", "items": []}
