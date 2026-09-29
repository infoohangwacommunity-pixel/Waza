"""
Durable student memory — storage only.

The AI decides when to search, create, update, supersede, or forget.
Infrastructure enforces principal isolation and persistence.
The AI calls search/get/create/update/supersede/forget; infrastructure stores.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import String, and_, cast, or_, select, text as sa_text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import Memory
from wax.observability.logging import get_logger

logger = get_logger(__name__)

# Mechanical transport ceilings only — not relevance policy.
MAX_TERMS = 12
MAX_TERM_LEN = 200
MAX_LIMIT = 100
SEARCHABLE_FIELDS = ("content", "tags", "structured")


def _as_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


def _row_to_dict(m: Memory) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "memory_type": m.memory_type,
        "content": m.content,
        "tags": list(m.tags or []),
        "structured": dict(m.structured or {}),
        "is_active": m.is_active,
        "validity_status": m.validity_status,
        "superseded_by_id": str(m.superseded_by_id) if m.superseded_by_id else None,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "updated_at": m.updated_at.isoformat() if getattr(m, "updated_at", None) else None,
    }


def _owned(m: Memory | None, principal_id: UUID) -> bool:
    """Strict isolation: row must exist and belong to principal."""
    return m is not None and m.principal_id == principal_id


def _clean_terms(terms: Any, query: Any) -> tuple[list[str], list[str]]:
    """Normalize the AI's explicit terms into plain strings + quoted phrases."""
    raw: list[Any] = []
    if isinstance(terms, str):
        raw = [terms]
    elif isinstance(terms, (list, tuple)):
        raw = list(terms)
    if not raw and query is not None and not isinstance(query, (dict, list)):
        raw = [str(query)]

    phrases: list[str] = []
    words: list[str] = []
    for item in raw:
        s = str(item or "").strip()
        if not s:
            continue
        # Quoted segments are exact substrings; the rest split into words.
        parts = s.split('"')
        for i, part in enumerate(parts):
            if i % 2 == 1:  # inside quotes → phrase
                p = part.strip()[:MAX_TERM_LEN]
                if p:
                    phrases.append(p)
                continue
            for w in part.split():
                w = w.strip("\"'`“”‘’").strip(".,;:!?()[]{}<>")[:MAX_TERM_LEN]
                if w:
                    words.append(w.lower())

    def _dedup(seq: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for x in seq:
            k = x.lower()
            if k not in seen:
                seen.add(k)
                out.append(x)
        return out

    phrases = _dedup(phrases)
    words = _dedup(words)
    total = len(phrases) + len(words)
    # Mechanical ceiling only: keep the AI's FIRST terms, drop the rest.
    # This is a transport bound, not relevance selection of stored data.
    kept_words = words[: max(0, MAX_TERMS - len(phrases))]
    dropped = total - len(phrases) - len(kept_words)
    return phrases + kept_words, (words[len(kept_words):] if dropped > 0 else [])


def _cast_json_text(column: Any, dialect_name: str) -> Any:
    """Cast a JSON/JSONB column to plain text so mechanical substring matching
    works identically on SQLite and Postgres. Plain SQL cast — no trigram,
    FTS, vector or similarity machinery."""
    return cast(column, String)


def _term_clause(column_expr: Any, term: str, dialect_name: str) -> Any:
    like = f"%{term}%"
    if dialect_name == "postgresql" and "%" in term:
        # Escape LIKE wildcards inside the AI's literal term (ILIKE pattern).
        like = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        return column_expr.ilike(like, escape="\\")
    return column_expr.ilike(like)


async def memory_search(
    session: AsyncSession,
    principal_id: Any,
    *,
    query: Any = None,
    terms: Any = None,
    mode: str = "any",
    fields: Any = None,
    tags: Any = None,
    memory_type: str | None = None,
    limit: int = 20,
    offset: int = 0,
    include_inactive: bool = False,
) -> dict[str, Any]:
    """Explicit mechanical search primitive over this principal's memories.

    The AI formulates the query; infrastructure applies plain textual filters
    only (case-insensitive substring match, boolean any/all composition) and
    returns matches ordered by recency. There is NO semantic ranking, NO
    embeddings, NO similarity score, NO application-defined relevance policy,
    and NO automatic selection of "top memories". Multiple investigative
    queries are expected: narrow with terms/tags/type, page with offset.

    Fields: any subset of content/tags/structured (default: all three).
    Tags filter: memory must carry ALL listed tags (exact, case-sensitive).
    """
    pid = _as_uuid(principal_id)
    if not pid:
        return {"ok": False, "error": "no_principal", "memories": []}

    mode = str(mode or "any").strip().lower()
    if mode not in ("any", "all"):
        return {"ok": False, "error": "mode must be 'any' or 'all'"}

    field_list = [str(f).strip().lower() for f in (fields or [])] if isinstance(fields, (list, tuple)) else ([str(fields).strip().lower()] if fields else [])
    field_list = [f for f in field_list if f in SEARCHABLE_FIELDS] or ["content"]

    all_terms, dropped_terms = _clean_terms(terms, query)

    tag_list = [str(t).strip() for t in (tags or [])] if isinstance(tags, (list, tuple)) else ([str(tags).strip()] if tags else [])
    tag_list = [t for t in tag_list if t]

    limit = max(1, min(int(limit or 20), MAX_LIMIT))
    offset = max(0, int(offset or 0))

    dialect_name = session.bind.dialect.name if session.bind is not None else ""
    stmt = select(Memory).where(Memory.principal_id == pid)
    if not include_inactive:
        stmt = stmt.where(Memory.is_active.is_(True))
    if memory_type:
        stmt = stmt.where(Memory.memory_type == str(memory_type)[:50])

    # JSON columns need an explicit text cast on Postgres for ILIKE.
    exprs = []
    for fname in field_list:
        col = getattr(Memory, fname)
        exprs.append(_cast_json_text(col, dialect_name))

    if all_terms:
        per_term = [
            or_(*[_term_clause(e, t, dialect_name) for e in exprs]) for t in all_terms
        ]
        stmt = stmt.where(and_(*per_term) if mode == "all" else or_(*per_term))

    if tag_list:
        if dialect_name == "postgresql":
            stmt = stmt.where(Memory.tags.op("@>")(sa_text(f"'{json.dumps(tag_list)}'::jsonb")))
        else:
            # SQLite: mechanical containment on the serialized JSON array text.
            for t_ in tag_list:
                stmt = stmt.where(
                    _cast_json_text(Memory.tags, dialect_name).like(f'%"{t_}"%')
                )

    stmt = stmt.order_by(Memory.updated_at.desc()).offset(offset).limit(limit)
    result = await session.execute(stmt)
    rows = list(result.scalars().all())

    out: dict[str, Any] = {
        "ok": True,
        "memories": [_row_to_dict(m) for m in rows],
        "count": len(rows),
        "principal_id": str(pid),
        "query_terms": all_terms,
        "mode": mode,
        "fields": field_list,
        "limit": limit,
        "offset": offset,
    }
    if dropped_terms:
        out["dropped_terms"] = dropped_terms
        out["note"] = f"mechanical term cap ({MAX_TERMS}) exceeded; first terms kept — re-query for the rest"
    if rows and len(rows) == limit:
        out["next_offset"] = offset + limit
    return out


async def memory_list(
    session: AsyncSession,
    principal_id: Any,
    *,
    memory_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
    include_inactive: bool = False,
) -> dict[str, Any]:
    """Plain browse of this principal's store, newest first.

    A mechanical listing capability (no filtering intelligence, no ranking):
    the AI may inspect its whole durable store or page through it."""
    pid = _as_uuid(principal_id)
    if not pid:
        return {"ok": False, "error": "no_principal", "memories": []}

    limit = max(1, min(int(limit or 50), MAX_LIMIT))
    offset = max(0, int(offset or 0))

    stmt = select(Memory).where(Memory.principal_id == pid)
    if not include_inactive:
        stmt = stmt.where(Memory.is_active.is_(True))
    if memory_type:
        stmt = stmt.where(Memory.memory_type == str(memory_type)[:50])
    stmt = stmt.order_by(Memory.updated_at.desc()).offset(offset).limit(limit)
    result = await session.execute(stmt)
    rows = list(result.scalars().all())

    out: dict[str, Any] = {
        "ok": True,
        "memories": [_row_to_dict(m) for m in rows],
        "count": len(rows),
        "principal_id": str(pid),
    }
    if rows and len(rows) == limit:
        out["next_offset"] = offset + limit
    return out


async def memory_get(
    session: AsyncSession,
    principal_id: Any,
    memory_id: str | None,
) -> dict[str, Any]:
    pid = _as_uuid(principal_id)
    mid = _as_uuid(memory_id)
    if not pid or not mid:
        return {"ok": False, "error": "principal_and_memory_id_required"}
    m = await session.get(Memory, mid)
    if not _owned(m, pid):
        return {"ok": False, "error": "not_found"}
    return {"ok": True, "memory": _row_to_dict(m)}


async def memory_create(
    session: AsyncSession,
    principal_id: Any,
    *,
    content: str,
    memory_type: str = "semantic",
    tags: list[str] | None = None,
    structured: dict[str, Any] | None = None,
    work_id: Any = None,
    source: str = "ai",
) -> dict[str, Any]:
    pid = _as_uuid(principal_id)
    if not pid:
        return {"ok": False, "error": "no_principal"}
    content = (content or "").strip()
    if not content:
        return {"ok": False, "error": "content_required"}

    m = Memory(
        id=uuid4(),
        principal_id=pid,
        memory_type=str(memory_type or "semantic")[:50],
        content=content[:8000],
        structured=structured or {},
        source=str(source or "ai")[:50],
        source_work_id=_as_uuid(work_id),
        is_active=True,
        validity_status="active",
        tags=[str(t)[:80] for t in (tags or [])][:20],
    )
    session.add(m)
    await session.flush()
    logger.info("memory_created", memory_id=str(m.id), principal_id=str(pid))
    return {"ok": True, "memory": _row_to_dict(m)}


async def memory_update(
    session: AsyncSession,
    principal_id: Any,
    memory_id: str | None,
    *,
    content: str | None = None,
    tags: list[str] | None = None,
    structured: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pid = _as_uuid(principal_id)
    mid = _as_uuid(memory_id)
    if not pid or not mid:
        return {"ok": False, "error": "principal_and_memory_id_required"}
    m = await session.get(Memory, mid)
    if not _owned(m, pid):
        return {"ok": False, "error": "not_found"}
    if content is not None:
        m.content = str(content).strip()[:8000]
    if tags is not None:
        m.tags = [str(t)[:80] for t in tags][:20]
    if structured is not None:
        m.structured = dict(structured)
    await session.flush()
    return {"ok": True, "memory": _row_to_dict(m)}


async def memory_supersede(
    session: AsyncSession,
    principal_id: Any,
    old_memory_id: str | None,
    *,
    new_content: str,
    memory_type: str | None = None,
    reason: str | None = None,
    work_id: Any = None,
) -> dict[str, Any]:
    """
    Replace an active memory: INSERT new row and flush, then mark old superseded.
    Order matters for FK integrity.
    """
    pid = _as_uuid(principal_id)
    old_id = _as_uuid(old_memory_id)
    if not pid or not old_id:
        return {"ok": False, "error": "principal_and_memory_id_required"}
    old = await session.get(Memory, old_id)
    if not _owned(old, pid):
        return {"ok": False, "error": "not_found"}
    if not (new_content or "").strip():
        return {"ok": False, "error": "new_content_required"}

    created = await memory_create(
        session,
        pid,
        content=new_content,
        memory_type=memory_type or old.memory_type,
        tags=list(old.tags or []),
        structured={
            **(old.structured or {}),
            "supersedes": str(old.id),
            "supersede_reason": (reason or "")[:500],
        },
        work_id=work_id,
        source="ai_supersede",
    )
    if not created.get("ok"):
        return created
    new_id = created["memory"]["id"]
    old.is_active = False
    old.validity_status = "superseded"
    old.superseded_by_id = _as_uuid(new_id)
    await session.flush()
    logger.info(
        "memory_superseded",
        old_id=str(old.id),
        new_id=new_id,
        principal_id=str(pid),
    )
    return {
        "ok": True,
        "old_memory_id": str(old.id),
        "new_memory": created["memory"],
        "reason": reason,
    }


async def memory_forget(
    session: AsyncSession,
    principal_id: Any,
    memory_id: str | None = None,
    *,
    query: str | None = None,
) -> dict[str, Any]:
    """Deactivate memory for this principal only."""
    pid = _as_uuid(principal_id)
    if not pid:
        return {"ok": False, "error": "no_principal"}
    forgotten: list[str] = []
    mid = _as_uuid(memory_id)
    if mid:
        m = await session.get(Memory, mid)
        if not _owned(m, pid):
            return {"ok": False, "error": "not_found"}
        m.is_active = False
        m.validity_status = "forgotten"
        forgotten.append(str(m.id))
        await session.flush()
        return {"ok": True, "forgotten_ids": forgotten}
    if query and str(query).strip():
        q = f"%{str(query).strip()[:200]}%"
        stmt = (
            select(Memory)
            .where(
                Memory.principal_id == pid,
                Memory.is_active.is_(True),
                Memory.content.ilike(q),
            )
            .limit(30)
        )
        result = await session.execute(stmt)
        for m in result.scalars().all():
            m.is_active = False
            m.validity_status = "forgotten"
            forgotten.append(str(m.id))
        await session.flush()
        return {"ok": True, "forgotten_ids": forgotten, "count": len(forgotten)}
    return {"ok": False, "error": "memory_id_or_query_required"}
