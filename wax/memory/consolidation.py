"""
Memory consolidation — asynchronous intelligence over durable memory.

Runs in the background. Never blocks the learner-facing tutor path.
Detects contradictions, merges near-duplicates, expires stale temporary facts,
and refreshes derived understanding.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import Memory
from wax.intelligence.providers import ChatMessage, CompletionRequest, get_intelligence
from wax.observability.logging import get_logger

logger = get_logger(__name__)

CONSOLIDATION_SYSTEM = """You are the memory consolidation intelligence for WAX Prep.

Given a batch of active memories about one learner, propose maintenance actions.

Rules:
- Only suggest actions that improve future tutoring.
- Prefer supersede over delete when a newer explicit statement replaces an older one.
- Mark temporary / outdated items for deactivation.
- Merge near-duplicates into one clearer memory.
- Do not invent new facts about the learner.
- Return pure JSON.

Schema:
{
  "deactivate_ids": ["uuid", ...],
  "supersessions": [{"old_id": "...", "new_content": "...", "confidence": 0.0-1.0}],
  "merges": [{"keep_id": "...", "drop_ids": ["..."], "merged_content": "..."}],
  "notes": "optional"
}
"""


class MemoryConsolidationService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.intelligence = get_intelligence()

    async def consolidate_principal(self, principal_id, limit: int = 40) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        stmt = (
            select(Memory)
            .where(Memory.principal_id == principal_id, Memory.is_active.is_(True))
            .order_by(Memory.importance.desc(), Memory.updated_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        memories = list(result.scalars().all())
        if len(memories) < 2:
            return {"actions": 0, "reason": "too_few"}

        # Expire already-due temporary memories without calling the model
        expired = 0
        for m in memories:
            if m.expires_at and m.expires_at <= now:
                m.is_active = False
                expired += 1
        if expired:
            await self.session.flush()

        active = [m for m in memories if m.is_active]
        if len(active) < 2:
            return {"actions": expired, "expired": expired}

        lines = []
        for m in active:
            lines.append(
                f"id={m.id} type={m.memory_type} c={m.confidence:.2f} i={m.importance:.2f} "
                f"src={m.source} | {m.content}"
            )
        prompt = "Active memories:\n" + "\n".join(lines)

        try:
            response = await self.intelligence.complete(
                CompletionRequest(
                    messages=[
                        ChatMessage(role="system", content=CONSOLIDATION_SYSTEM),
                        ChatMessage(role="user", content=prompt),
                    ],
                    temperature=0.1,
                    max_tokens=1200,
                ),
                use_memory_model=True,
            )
            data = self._safe_json(response.content or "{}")
        except Exception as e:
            logger.error("consolidation_model_failed", error=str(e))
            return {"actions": expired, "expired": expired, "error": str(e)}

        actions = expired
        id_map = {str(m.id): m for m in active}

        for mid in data.get("deactivate_ids") or []:
            m = id_map.get(str(mid))
            if m:
                m.is_active = False
                actions += 1

        for item in data.get("supersessions") or []:
            old = id_map.get(str(item.get("old_id")))
            content = (item.get("new_content") or "").strip()
            if old and content:
                old.is_active = False
                new_m = Memory(
                    id=uuid4(),
                    principal_id=principal_id,
                    memory_type=old.memory_type,
                    content=content,
                    confidence=float(item.get("confidence", 0.85)),
                    importance=old.importance,
                    source="inferred",
                    evidence=[
                        {
                            "action": "consolidation_supersede",
                            "old_id": str(old.id),
                            "at": now.isoformat(),
                        }
                    ],
                    is_active=True,
                    tags=old.tags or [],
                )
                # FK: new memory row must exist before superseded_by_id points to it
                self.session.add(new_m)
                await self.session.flush()
                old.superseded_by_id = new_m.id
                actions += 1
                logger.info(
                    "memory_consolidation_supersede",
                    principal_id=str(principal_id),
                    old_id=str(old.id),
                    new_id=str(new_m.id),
                )

        for merge in data.get("merges") or []:
            keep = id_map.get(str(merge.get("keep_id")))
            merged_content = (merge.get("merged_content") or "").strip()
            if keep and merged_content:
                keep.content = merged_content
                keep.evidence = list(keep.evidence or []) + [
                    {"action": "merge", "at": now.isoformat()}
                ]
                for drop_id in merge.get("drop_ids") or []:
                    drop = id_map.get(str(drop_id))
                    if drop and drop.id != keep.id:
                        drop.is_active = False
                        # keep already exists in DB — safe FK target
                        drop.superseded_by_id = keep.id
                        actions += 1

        await self.session.flush()
        logger.info("consolidation_done", principal_id=str(principal_id), actions=actions)
        return {"actions": actions, "expired": expired}

    def _safe_json(self, text: str) -> dict[str, Any]:
        text = (text or "").strip()
        if text.startswith("```"):
            parts = text.split("```")
            text = parts[1] if len(parts) > 1 else text
            if text.startswith("json"):
                text = text[4:]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    pass
            return {}
