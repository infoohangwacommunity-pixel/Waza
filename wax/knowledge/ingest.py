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
        """Principal-scoped chunks only. Simple recency + optional substring filter."""
        stmt = (
            select(DocumentChunk)
            .where(DocumentChunk.principal_id == principal_id)
            .order_by(DocumentChunk.created_at.desc())
            .limit(40)
        )
        result = await self.session.execute(stmt)
        rows = list(result.scalars().all())
        q = (query or "").lower().strip()
        out: list[str] = []
        for ch in rows:
            content = (ch.content or "").strip()
            if not content:
                continue
            if q and q not in content.lower():
                continue
            out.append(content[:600])
            if len(out) >= limit:
                break
        if not out and rows:
            # fallback: most recent regardless of query
            for ch in rows[:limit]:
                if ch.content:
                    out.append(ch.content[:600])
        return out
