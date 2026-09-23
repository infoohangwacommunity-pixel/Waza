"""Ingest learner-provided materials into KnowledgeSource + chunks. No product curriculum."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import DocumentChunk, KnowledgeSource
from wax.memory.embeddings import embed_texts
from wax.observability.logging import get_logger

logger = get_logger(__name__)


def _chunk_text(text: str, size: int = 800) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    chunks = []
    i = 0
    while i < len(text):
        chunks.append(text[i : i + size])
        i += size
    return chunks


class KnowledgeIngestService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def ingest_text(
        self,
        principal_id,
        title: str,
        text: str,
        kind: str = "text",
    ) -> KnowledgeSource:
        source = KnowledgeSource(
            id=uuid4(),
            principal_id=principal_id,
            kind=kind,
            title=title[:500],
            status="extracted",
            text_extract=text[:200000],
        )
        self.session.add(source)
        await self.session.flush()
        parts = _chunk_text(text)
        vectors = await embed_texts(parts) if parts else None
        for i, part in enumerate(parts):
            ch = DocumentChunk(
                id=uuid4(),
                principal_id=principal_id,
                knowledge_source_id=source.id,
                ordinal=i,
                content=part,
                embedding=(vectors[i] if vectors and i < len(vectors) else None),
            )
            self.session.add(ch)
        source.status = "ready"
        await self.session.flush()
        logger.info("knowledge_ingested", source_id=str(source.id), chunks=len(parts))
        return source

    async def list_sources(self, principal_id, limit: int = 12) -> list[dict[str, Any]]:
        stmt = (
            select(KnowledgeSource)
            .where(KnowledgeSource.principal_id == principal_id)
            .order_by(KnowledgeSource.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        rows = list(result.scalars().all())
        return [
            {
                "id": str(s.id),
                "title": s.title,
                "kind": s.kind,
                "status": s.status,
            }
            for s in rows
        ]

    async def recent_chunks_for_context(
        self, principal_id, query: str | None = None, limit: int = 6
    ) -> list[str]:
        """Prefer semantic search when query present; else recent chunks."""
        if query and query.strip():
            hits = await self.semantic_search(principal_id, query, limit=limit)
            if hits:
                return [h["content"][:600] for h in hits]
        stmt = (
            select(DocumentChunk)
            .where(DocumentChunk.principal_id == principal_id)
            .order_by(DocumentChunk.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        rows = list(result.scalars().all())
        return [(ch.content or "")[:600] for ch in rows if ch.content]

    async def semantic_search(
        self, principal_id, query: str, *, limit: int = 6
    ) -> list[dict]:
        """
        Semantic + lexical retrieval of learner-owned chunks.
        Returns provenance-rich hits. Never cross-principal.
        """
        from wax.memory.embeddings import embed_one, cosine_similarity

        stmt = (
            select(DocumentChunk)
            .where(DocumentChunk.principal_id == principal_id)
            .order_by(DocumentChunk.created_at.desc())
            .limit(80)
        )
        result = await self.session.execute(stmt)
        rows = list(result.scalars().all())
        if not rows:
            return []
        q = (query or "").strip()
        q_lower = q.lower()
        q_vec = None
        try:
            if q:
                q_vec = embed_one(q)
        except Exception:
            q_vec = None

        scored: list[tuple[float, object]] = []
        for ch in rows:
            content = (ch.content or "").strip()
            if not content:
                continue
            score = 0.0
            if q_lower and q_lower in content.lower():
                score += 0.45
            # token overlap
            if q_lower:
                q_toks = set(q_lower.split())
                c_toks = set(content.lower().split())
                if q_toks:
                    score += 0.25 * (len(q_toks & c_toks) / max(1, len(q_toks)))
            if q_vec and getattr(ch, "embedding", None):
                try:
                    sim = cosine_similarity(q_vec, list(ch.embedding))
                    score += 0.55 * float(sim)
                except Exception:
                    pass
            if score <= 0 and not q:
                score = 0.1  # recency-only
            if score > 0:
                scored.append((score, ch))
        scored.sort(key=lambda x: x[0], reverse=True)
        out = []
        for score, ch in scored[:limit]:
            label = None
            try:
                src = await self.session.get(KnowledgeSource, ch.knowledge_source_id) if ch.knowledge_source_id else None
                if src:
                    label = getattr(src, "title", None) or getattr(src, "label", None) or getattr(src, "filename", None)
            except Exception:
                pass
            out.append(
                {
                    "content": (ch.content or "")[:1200],
                    "score": round(float(score), 4),
                    "chunk_id": str(ch.id),
                    "source_id": str(ch.knowledge_source_id) if ch.knowledge_source_id else None,
                    "label": label,
                }
            )
        return out

    async def content_fingerprint(self, text: str) -> str:
        import hashlib
        norm = " ".join((text or "").split()).lower()
        return hashlib.sha256(norm.encode("utf-8")).hexdigest()

