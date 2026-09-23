"""
WAX Prep Advanced Memory Subsystem.

The most important continuity layer of the product.

Capabilities:
- Multi-level memory (episodic, semantic, learning, preference, goal, behavioral, relationship)
- Confidence + provenance + evidence trail
- Intelligent extraction via smaller model
- Hybrid retrieval (structured filters + semantic ranking + recency + importance + goal relevance)
- Contradiction detection and supersession
- Expiration of temporary facts
- Isolation: memory failure never blocks the tutor response
- Learner-visible memory ("what do you remember about me?")
- Explicit forget / correct

Nothing is hardcoded as "subject" or "exam mode".
The AI decides what is worth remembering.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.config import get_settings
from wax.db.models import LearningObservation, Memory
from wax.intelligence.providers import ChatMessage, CompletionRequest, get_intelligence
from wax.memory.embeddings import embed_one, cosine_similarity
from wax.observability.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


EXTRACTION_SYSTEM = """You are the memory intelligence of WAX Prep — a persistent adaptive tutor.

Your only job: decide what is worth remembering from the latest interaction so the tutor can serve this learner better later.

Rules:
- Only extract information that will help future tutoring.
- Do NOT extract pure noise ("hi", "ok", "thanks", "lol").
- Temporary states ("I'm tired today") get short expiry (hours).
- Stable preferences, goals, knowledge observations, important events persist.
- Distinguish source: "explicit" (user said clearly), "inferred" (you deduced), "observed" (from behavior).
- Assign confidence 0.0–1.0 and importance 0.0–1.0.
- If the user contradicts an earlier preference, include a supersedes_hint so the system can retire the old memory.
- Return pure JSON only. No markdown fences.

Output schema:
{
  "memories": [
    {
      "memory_type": "preference|semantic|learning|goal|episodic|behavioral|relationship",
      "content": "clear natural language statement about the learner",
      "structured": {},
      "confidence": 0.0-1.0,
      "importance": 0.0-1.0,
      "source": "explicit|inferred|observed",
      "expires_in_hours": null or number,
      "tags": ["tag1", "tag2"],
      "supersedes_hint": "optional text describing what this replaces"
    }
  ],
  "learning_observations": [
    {
      "concept_key": "stable-key-slug",
      "concept_label": "human readable label",
      "mastery_delta": -0.3 to +0.3,
      "notes": "optional"
    }
  ],
  "goal_updates": [
    {
      "action": "create|complete|pause|abandon",
      "title": "...",
      "reason": "..."
    }
  ]
}
"""


RETRIEVAL_PLANNER_SYSTEM = """You are the memory retrieval planner for WAX Prep.

Given the current learner message and a short summary of available memory types, decide which memories would materially help the tutor respond well.

Return pure JSON:
{
  "query_terms": ["term1", "term2"],
  "preferred_types": ["preference", "goal", "learning", "episodic"],
  "need_history": true/false,
  "reason": "one sentence"
}
"""


class MemoryService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.intelligence = get_intelligence()

    # ─────────────────────────────────────────────
    # EXTRACTION
    # ─────────────────────────────────────────────

    async def extract_and_store(
        self,
        principal_id,
        conversation_id: str | None,
        recent_messages: list[dict[str, str]],
        source_message_id=None,
        source_work_id=None,
    ) -> list[Memory]:
        """
        Extract memories from recent interaction.
        Failures are logged and isolated — never raise to the tutor path.
        """
        try:
            prompt = self._build_extraction_prompt(recent_messages)
            response = await self.intelligence.complete(
                CompletionRequest(
                    messages=[
                        ChatMessage(role="system", content=EXTRACTION_SYSTEM),
                        ChatMessage(role="user", content=prompt),
                    ],
                    temperature=0.15,
                    max_tokens=1800,
                ),
                use_memory_model=True,
                allow_fallback=True,
            )
            if not response.content:
                return []

            data = self._safe_json(response.content)
            stored: list[Memory] = []

            for item in data.get("memories", []):
                mem = await self._store_memory(
                    principal_id=principal_id,
                    item=item,
                    source_message_id=source_message_id,
                    source_work_id=source_work_id,
                )
                if mem:
                    stored.append(mem)

            for obs in data.get("learning_observations", []):
                await self._upsert_learning_observation(principal_id, obs)

            await self.session.flush()
            logger.info(
                "memory_extraction_done",
                principal_id=str(principal_id),
                count=len(stored),
            )
            return stored
        except Exception as e:
            logger.error(
                "memory_extraction_failed",
                principal_id=str(principal_id),
                error=str(e),
                error_class="memory_failure",
            )
            return []

    # ─────────────────────────────────────────────
    # HYBRID RETRIEVAL (the advanced part)
    # ─────────────────────────────────────────────

    async def retrieve_relevant(
        self,
        principal_id,
        query: str,
        *,
        limit: int = 18,
        memory_types: list[str] | None = None,
        min_confidence: float = 0.25,
        active_goal_hint: str | None = None,
    ) -> list[Memory]:
        """
        Hybrid retrieval:
        1. Structured filter (active, not expired, confidence, optional type)
        2. Oversample by importance + recency
        3. Score by keyword overlap + tag match + importance + confidence + goal relevance
        4. Return top-k

        Later upgrade path: embeddings / pgvector without changing this interface.
        """
        now = datetime.now(timezone.utc)
        stmt = (
            select(Memory)
            .where(
                Memory.principal_id == principal_id,
                Memory.is_active.is_(True),
                Memory.confidence >= min_confidence,
                or_(Memory.expires_at.is_(None), Memory.expires_at > now),
            )
            .order_by(Memory.importance.desc(), Memory.updated_at.desc())
            .limit(limit * 8)
        )
        if memory_types:
            stmt = stmt.where(Memory.memory_type.in_(memory_types))

        result = await self.session.execute(stmt)
        candidates = list(result.scalars().all())
        if not candidates:
            return []

        query_lower = (query or "").lower()
        tokens = [t for t in re.split(r"\W+", query_lower) if len(t) > 2]
        goal_lower = (active_goal_hint or "").lower()

        query_vec = None
        try:
            query_vec = await embed_one(query)
        except Exception:
            query_vec = None

        scored: list[tuple[float, Memory]] = []
        for m in candidates:
            score = m.importance * 0.35 + m.confidence * 0.20
            content_lower = m.content.lower()
            hits = sum(1 for tok in tokens if tok in content_lower)
            score += min(0.30, hits * 0.07)
            for tag in m.tags or []:
                if tag.lower() in query_lower:
                    score += 0.10
            if goal_lower and any(tok in content_lower for tok in goal_lower.split() if len(tok) > 3):
                score += 0.12
            age_hours = (now - (m.updated_at or m.created_at)).total_seconds() / 3600
            if age_hours < 24:
                score += 0.10
            elif age_hours < 24 * 14:
                score += 0.05
            if m.memory_type in ("learning", "preference", "goal"):
                score += 0.05
            # Semantic boost when embeddings exist
            if query_vec and m.embedding:
                try:
                    sim = cosine_similarity(query_vec, list(m.embedding))
                    score += max(0.0, sim) * 0.45
                except Exception:
                    pass
            scored.append((score, m))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in scored[:limit]]

    async def plan_and_retrieve(
        self,
        principal_id,
        user_message: str,
        *,
        limit: int = 15,
    ) -> list[Memory]:
        """
        Optional intelligent retrieval planner.
        Uses memory model to decide what kinds of memories matter for this turn.
        Falls back to hybrid retrieve on any failure.
        """
        try:
            response = await self.intelligence.complete(
                CompletionRequest(
                    messages=[
                        ChatMessage(role="system", content=RETRIEVAL_PLANNER_SYSTEM),
                        ChatMessage(
                            role="user",
                            content=f"Current learner message:\n{user_message}\n\nDecide retrieval plan.",
                        ),
                    ],
                    temperature=0.1,
                    max_tokens=300,
                ),
                use_memory_model=True,
            )
            plan = self._safe_json(response.content or "{}")
            types = plan.get("preferred_types") or None
            terms = plan.get("query_terms") or []
            query = " ".join(terms) if terms else user_message
            return await self.retrieve_relevant(
                principal_id, query, limit=limit, memory_types=types
            )
        except Exception:
            return await self.retrieve_relevant(principal_id, user_message, limit=limit)

    async def get_active_summary(self, principal_id, limit: int = 16) -> str:
        """Human-readable memory summary for the context assembler."""
        memories = await self.retrieve_relevant(
            principal_id,
            query="learner profile preferences goals knowledge strengths difficulties patterns",
            limit=limit,
            min_confidence=0.35,
        )
        if not memories:
            return "No prior durable memories about this learner yet. This is a new or sparse relationship."

        lines = []
        for m in memories:
            conf = f"{m.confidence:.2f}"
            imp = f"{m.importance:.2f}"
            lines.append(f"- [{m.memory_type}|c={conf}|i={imp}|src={m.source}] {m.content}")
        return "\n".join(lines)

    # ─────────────────────────────────────────────
    # USER CONTROL
    # ─────────────────────────────────────────────

    async def list_for_user(self, principal_id, limit: int = 40) -> list[Memory]:
        now = datetime.now(timezone.utc)
        stmt = (
            select(Memory)
            .where(
                Memory.principal_id == principal_id,
                Memory.is_active.is_(True),
                or_(Memory.expires_at.is_(None), Memory.expires_at > now),
            )
            .order_by(Memory.importance.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def forget(self, principal_id, memory_id) -> bool:
        mem = await self.session.get(Memory, memory_id)
        if mem and mem.principal_id == principal_id:
            mem.is_active = False
            mem.metadata_ = {**(mem.metadata_ or {}), "forgotten_by_user": True}
            await self.session.flush()
            return True
        return False

    async def supersede(
        self,
        principal_id,
        old_memory_id,
        new_content: str,
        *,
        source: str = "explicit",
        confidence: float = 0.9,
    ) -> Memory:
        old = await self.session.get(Memory, old_memory_id)
        if old and old.principal_id == principal_id:
            old.is_active = False

        new_mem = Memory(
            id=uuid4(),
            principal_id=principal_id,
            memory_type=old.memory_type if old else "semantic",
            content=new_content,
            confidence=confidence,
            importance=old.importance if old else 0.7,
            source=source,
            evidence=[{"action": "supersede", "old_id": str(old_memory_id)}],
            is_active=True,
        )
        if old:
            old.superseded_by_id = new_mem.id
        self.session.add(new_mem)
        await self.session.flush()
        return new_mem

    # ─────────────────────────────────────────────
    # INTERNAL
    # ─────────────────────────────────────────────

    def _build_extraction_prompt(self, messages: list[dict[str, str]]) -> str:
        lines = ["Recent conversation:"]
        for m in messages[-14:]:
            role = m.get("role", "user").upper()
            lines.append(f"{role}: {m.get('content', '')}")
        lines.append(
            "\nExtract only what is worth remembering for future tutoring of this person."
        )
        return "\n".join(lines)

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
            # try to find first { ... }
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    pass
            return {}

    
    async def _find_similar_memory(self, principal_id, content: str, memory_type: str):
        """Dedup before insert — reinforce instead of duplicate."""
        from sqlalchemy import select
        from wax.db.models import Memory
        from wax.memory.embeddings import embed_one, cosine_similarity
        stmt = (
            select(Memory)
            .where(
                Memory.principal_id == principal_id,
                Memory.is_active.is_(True),
                Memory.memory_type == memory_type,
            )
            .order_by(Memory.updated_at.desc())
            .limit(30)
        )
        result = await self.session.execute(stmt)
        candidates = list(result.scalars().all())
        content_l = (content or "").lower().strip()
        for m in candidates:
            if (m.content or "").lower().strip() == content_l:
                return m
            # simple token overlap
            a = set(content_l.split())
            b = set((m.content or "").lower().split())
            if a and b and len(a & b) / max(1, len(a | b)) > 0.72:
                return m
        # embedding similarity if available
        try:
            qv = await embed_one(content)
            if qv:
                best = None
                best_sim = 0.88
                for m in candidates:
                    if m.embedding:
                        sim = cosine_similarity(qv, list(m.embedding))
                        if sim > best_sim:
                            best_sim = sim
                            best = m
                if best:
                    return best
        except Exception:
            pass
        return None

    async def _store_memory(
        self,
        principal_id,
        item: dict[str, Any],
        source_message_id,
        source_work_id,
    ) -> Memory | None:
        content = (item.get("content") or "").strip()
        if not content or len(content) < 4:
            return None

        expires_at = None
        hours = item.get("expires_in_hours")
        if hours is not None:
            try:
                expires_at = datetime.now(timezone.utc) + timedelta(hours=float(hours))
            except (TypeError, ValueError):
                pass

        mem = Memory(
            id=uuid4(),
            principal_id=principal_id,
            memory_type=item.get("memory_type", "semantic"),
            content=content,
            structured=item.get("structured") or {},
            confidence=float(item.get("confidence", 0.6)),
            importance=float(item.get("importance", 0.5)),
            source=item.get("source", "inferred"),
            source_message_id=source_message_id,
            source_work_id=source_work_id,
            evidence=[{"extracted_at": datetime.now(timezone.utc).isoformat()}],
            is_active=True,
            expires_at=expires_at,
            tags=item.get("tags") or [],
        )
        memory_type = item.get("memory_type", "semantic")
        importance = float(item.get("importance", 0.5))
        hint = (item.get("supersedes_hint") or "").strip()
        if hint:
            try:
                from wax.learner.contradiction import EvidenceView, resolve_conflict

                cands = await self.retrieve_relevant(
                    principal_id, hint, limit=6, memory_types=[memory_type] if memory_type else None
                )
                views = []
                for c in cands:
                    views.append(
                        EvidenceView(
                            id=str(c.id),
                            content=c.content or "",
                            kind=c.memory_type or "semantic",
                            source=c.source or "inferred",
                            confidence=float(c.confidence or 0.5),
                            recency=0.4,
                            is_correction=False,
                            is_active=bool(c.is_active),
                        )
                    )
                views.append(
                    EvidenceView(
                        id="incoming",
                        content=content,
                        kind=memory_type,
                        source=item.get("source") or "inferred",
                        confidence=float(item.get("confidence", 0.6)),
                        recency=1.0,
                        is_correction=True,
                    )
                )
                resolved = resolve_conflict(views)
                if resolved.get("status") in ("resolved", "current") and cands:
                    winner_is_new = resolved["current"] and resolved["current"].id == "incoming"
                    if winner_is_new:
                        new_mem = await self.supersede(
                            principal_id,
                            cands[0].id,
                            content,
                            source="learner_correction",
                            confidence=float(item.get("confidence", 0.85)),
                        )
                        logger.info("memory_superseded_via_hint", old_id=str(cands[0].id))
                        return new_mem
            except Exception:
                logger.exception("supersedes_hint_failed")

        similar = await self._find_similar_memory(principal_id, content, memory_type)
        if similar and not hint:
            similar.confidence = min(1.0, (similar.confidence or 0.5) + 0.05)
            similar.importance = max(similar.importance or 0.5, importance)
            similar.evidence = list(similar.evidence or []) + [{"kind": "reinforcement"}]
            similar.last_observed_at = datetime.now(timezone.utc)
            await self.session.flush()
            return similar
        self.session.add(mem)
        await self.session.flush()
        try:
            vec = await embed_one(content)
            if vec:
                mem.embedding = vec
                await self.session.flush()
        except Exception:
            pass
        return mem

    async def _upsert_learning_observation(
        self, principal_id, obs: dict[str, Any]
    ) -> None:
        key = obs.get("concept_key")
        if not key:
            return
        stmt = select(LearningObservation).where(
            LearningObservation.principal_id == principal_id,
            LearningObservation.concept_key == key,
        )
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()
        delta = float(obs.get("mastery_delta", 0.0))
        if existing:
            existing.mastery = max(0.0, min(1.0, existing.mastery + delta))
            existing.attempts += 1
            if delta > 0:
                existing.successes += 1
            existing.last_observed_at = datetime.now(timezone.utc)
            if obs.get("notes"):
                existing.notes = obs["notes"]
        else:
            new_obs = LearningObservation(
                id=uuid4(),
                principal_id=principal_id,
                concept_key=key,
                concept_label=obs.get("concept_label") or key,
                mastery=max(0.0, min(1.0, 0.3 + delta)),
                confidence=0.4,
                attempts=1,
                successes=1 if delta > 0 else 0,
                last_observed_at=datetime.now(timezone.utc),
                notes=obs.get("notes"),
            )
            self.session.add(new_obs)
