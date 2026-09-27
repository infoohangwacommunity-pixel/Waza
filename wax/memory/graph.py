"""
Multi-layer episodic memory graph.

Layers (on Memory.layer):
  working      — transient turn state (short TTL)
  episodic     — what happened in a bounded episode
  semantic     — stable facts about the learner
  procedural   — how this learner learns / what works
  goal         — objectives and progress
  relationship — rapport / continuity of the tutor–learner bond

Episodes group experience. Links form a directed graph among memories and episodes.
Retrieval walks: relevance → neighborhood → layer budget → compact package.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

def _log():
    try:
        from wax.observability.logging import get_logger
        return get_logger(__name__)
    except Exception:
        import logging
        return logging.getLogger(__name__)


class _L:
    def exception(self, *a, **k):
        _log().exception(*a, **k)

    def info(self, *a, **k):
        _log().info(*a, **k)


logger = _L()

LAYERS = (
    "working",
    "episodic",
    "semantic",
    "procedural",
    "goal",
    "relationship",
)

# Default package budget per layer (max items)
def _sa():
    from sqlalchemy import or_, select
    from wax.db.models import Memory, MemoryEpisode, MemoryLink
    return or_, select, Memory, MemoryEpisode, MemoryLink


DEFAULT_LAYER_BUDGET = {
    "relationship": 2,
    "goal": 3,
    "procedural": 3,
    "semantic": 6,
    "episodic": 5,
    "working": 2,
}

TYPE_TO_LAYER = {
    "preference": "relationship",
    "relationship": "relationship",
    "goal": "goal",
    "learning": "procedural",
    "behavioral": "procedural",
    "episodic": "episodic",
    "semantic": "semantic",
}


def layer_for_memory_type(memory_type: str | None) -> str:
    return TYPE_TO_LAYER.get((memory_type or "").lower(), "semantic")


class MemoryGraphService:
    def __init__(self, session):
        self.session = session

    async def open_or_continue_episode(
        self,
        principal_id,
        *,
        conversation_id=None,
        work_id=None,
        channel: str = "",
        title: str = "",
        summary: str = "",
        max_idle_hours: float = 6.0,
    ) -> MemoryEpisode:
        """Reuse an open episode on the same conversation if recently active."""
        or_, select, Memory, MemoryEpisode, MemoryLink = _sa()
        now = datetime.now(timezone.utc)
        stmt = (
            select(MemoryEpisode)
            .where(
                MemoryEpisode.principal_id == principal_id,
                MemoryEpisode.status == "open",
            )
            .order_by(MemoryEpisode.last_active_at.desc().nullslast())
            .limit(5)
        )
        # conversation_id may arrive as str from payloads
        try:
            from uuid import UUID as _UUID
            if conversation_id and not isinstance(conversation_id, _UUID):
                conversation_id = _UUID(str(conversation_id))
            if work_id and not isinstance(work_id, _UUID):
                work_id = _UUID(str(work_id))
        except Exception:
            conversation_id = conversation_id
            work_id = work_id
        if conversation_id:
            stmt = stmt.where(
                or_(
                    MemoryEpisode.conversation_id == conversation_id,
                    MemoryEpisode.conversation_id.is_(None),
                )
            )
        rows = list((await self.session.execute(stmt)).scalars().all())
        for ep in rows:
            if conversation_id and ep.conversation_id and ep.conversation_id != conversation_id:
                continue
            last = ep.last_active_at or ep.updated_at or ep.started_at
            if last:
                age_h = (now - last).total_seconds() / 3600.0
                if age_h <= max_idle_hours:
                    ep.last_active_at = now
                    if work_id:
                        ep.work_id = work_id
                    if summary and not ep.summary:
                        ep.summary = summary[:2000]
                    await self.session.flush()
                    return ep

        ep = MemoryEpisode(
            id=uuid4(),
            principal_id=principal_id,
            conversation_id=conversation_id,
            work_id=work_id,
            title=(title or "Learning session")[:500],
            summary=(summary or "")[:2000] or None,
            channel=(channel or "")[:40] or None,
            importance=0.5,
            status="open",
            started_at=now,
            last_active_at=now,
        )
        self.session.add(ep)
        await self.session.flush()
        return ep

    async def attach_memory(
        self,
        memory: Memory,
        episode: MemoryEpisode | None,
        *,
        layer: str | None = None,
    ) -> Memory:
        if layer:
            memory.layer = layer if layer in LAYERS else layer_for_memory_type(memory.memory_type)
        elif not getattr(memory, "layer", None):
            memory.layer = layer_for_memory_type(memory.memory_type)
        or_, select, Memory, MemoryEpisode, MemoryLink = _sa()
        if episode is not None:
            memory.episode_id = episode.id
            # member_of_episode link
            link = MemoryLink(
                id=uuid4(),
                principal_id=memory.principal_id,
                from_memory_id=memory.id,
                episode_id=episode.id,
                relation="member_of_episode",
                strength=0.9,
            )
            self.session.add(link)
        await self.session.flush()
        return memory

    async def link(
        self,
        principal_id,
        *,
        relation: str,
        from_memory_id=None,
        to_memory_id=None,
        episode_id=None,
        strength: float = 0.5,
        evidence: list | None = None,
    ) -> MemoryLink:
        or_, select, Memory, MemoryEpisode, MemoryLink = _sa()
        link = MemoryLink(
            id=uuid4(),
            principal_id=principal_id,
            from_memory_id=from_memory_id,
            to_memory_id=to_memory_id,
            episode_id=episode_id,
            relation=(relation or "related")[:60],
            strength=float(strength),
            evidence=evidence or [],
            is_active=True,
        )
        self.session.add(link)
        await self.session.flush()
        return link

    async def auto_link_similar(
        self,
        principal_id,
        new_memory: Memory,
        *,
        limit: int = 8,
    ) -> list[MemoryLink]:
        """Connect new memory to high-overlap existing actives (no extra LLM)."""
        or_, select, Memory, MemoryEpisode, MemoryLink = _sa()
        stmt = (
            select(Memory)
            .where(
                Memory.principal_id == principal_id,
                Memory.is_active.is_(True),
                Memory.id != new_memory.id,
            )
            .order_by(Memory.importance.desc())
            .limit(40)
        )
        cands = list((await self.session.execute(stmt)).scalars().all())
        a = set((new_memory.content or "").lower().split())
        links: list[MemoryLink] = []
        for m in cands:
            b = set((m.content or "").lower().split())
            if not a or not b:
                continue
            overlap = len(a & b) / max(1, len(a | b))
            if overlap < 0.28:
                continue
            rel = "elaborates" if overlap > 0.5 else "related"
            if m.memory_type == "learning" and new_memory.memory_type == "learning":
                rel = "about_concept"
            link = await self.link(
                principal_id,
                relation=rel,
                from_memory_id=new_memory.id,
                to_memory_id=m.id,
                strength=min(0.95, 0.4 + overlap),
            )
            links.append(link)
            if len(links) >= limit:
                break
        return links

    async def retrieve_graph_package(
        self,
        principal_id,
        query: str = "",
        *,
        layer_budget: dict[str, int] | None = None,
        seed_limit: int = 12,
    ) -> dict[str, Any]:
        """
        Layer-budgeted retrieval with one-hop neighborhood expansion.
        """
        or_, select, Memory, MemoryEpisode, MemoryLink = _sa()
        from wax.memory.service import MemoryService

        budget = dict(DEFAULT_LAYER_BUDGET)
        if layer_budget:
            budget.update(layer_budget)

        seeds = await MemoryService(self.session).retrieve_relevant(
            principal_id, query=query or "", limit=seed_limit
        )
        by_id = {m.id: m for m in seeds}
        # Expand one hop
        if seeds:
            ids = [m.id for m in seeds]
            link_stmt = (
                select(MemoryLink)
                .where(
                    MemoryLink.principal_id == principal_id,
                    MemoryLink.is_active.is_(True),
                    or_(
                        MemoryLink.from_memory_id.in_(ids),
                        MemoryLink.to_memory_id.in_(ids),
                    ),
                )
                .limit(40)
            )
            links = list((await self.session.execute(link_stmt)).scalars().all())
            neighbor_ids = set()
            for lk in links:
                if lk.from_memory_id:
                    neighbor_ids.add(lk.from_memory_id)
                if lk.to_memory_id:
                    neighbor_ids.add(lk.to_memory_id)
            neighbor_ids -= set(ids)
            if neighbor_ids:
                n_stmt = select(Memory).where(
                    Memory.id.in_(list(neighbor_ids)[:30]),
                    Memory.is_active.is_(True),
                )
                for m in (await self.session.execute(n_stmt)).scalars().all():
                    by_id[m.id] = m
        else:
            links = []

        # Open episodes
        ep_stmt = (
            select(MemoryEpisode)
            .where(
                MemoryEpisode.principal_id == principal_id,
                MemoryEpisode.status.in_(["open", "paused"]),
            )
            .order_by(MemoryEpisode.last_active_at.desc().nullslast())
            .limit(3)
        )
        episodes = list((await self.session.execute(ep_stmt)).scalars().all())

        layered: dict[str, list[Memory]] = {k: [] for k in LAYERS}
        for m in by_id.values():
            layer = getattr(m, "layer", None) or layer_for_memory_type(m.memory_type)
            if layer not in layered:
                layer = "semantic"
            layered[layer].append(m)

        package: dict[str, list[dict[str, Any]]] = {}
        total = 0
        for layer, cap in budget.items():
            items = layered.get(layer) or []
            items.sort(key=lambda x: (x.importance, x.confidence), reverse=True)
            package[layer] = [
                {
                    "id": str(m.id),
                    "type": m.memory_type,
                    "layer": layer,
                    "content": (m.content or "")[:400],
                    "importance": m.importance,
                    "confidence": m.confidence,
                }
                for m in items[:cap]
            ]
            total += len(package[layer])

        return {
            "ok": True,
            "query": (query or "")[:200],
            "total": total,
            "layers": package,
            "episodes": [
                {
                    "id": str(e.id),
                    "title": e.title,
                    "summary": (e.summary or "")[:300],
                    "status": e.status,
                    "channel": e.channel,
                }
                for e in episodes
            ],
            "link_count": len(links),
        }

    def render_package_text(self, package: dict[str, Any], *, max_chars: int = 2200) -> str:
        if not package or not package.get("layers"):
            return ""
        lines = ["\n--- Memory graph (multi-layer) ---"]
        for ep in package.get("episodes") or []:
            lines.append(f"Episode[{ep.get('status')}]: {ep.get('title')} — {ep.get('summary') or ''}")
        for layer in ("relationship", "goal", "procedural", "semantic", "episodic", "working"):
            items = (package.get("layers") or {}).get(layer) or []
            if not items:
                continue
            lines.append(f"[{layer}]")
            for it in items:
                lines.append(f"- {it.get('content')}")
        lines.append("--- End memory graph ---\n")
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[: max_chars - 20] + "\n…\n"
        return text
