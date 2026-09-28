"""
Durable student memory — storage only.

The AI decides when to search, create, update, supersede, or forget.
Infrastructure enforces principal isolation and persistence.
No automatic extraction, consolidation, injection, or hidden memory agent.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import Memory
from wax.observability.logging import get_logger

logger = get_logger(__name__)


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
        "confidence": m.confidence,
        "importance": m.importance,
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


async def memory_search(
    session: AsyncSession,
    principal_id: Any,
    *,
    query: str | None = None,
    memory_type: str | None = None,
    limit: int = 20,
    include_inactive: bool = False,
) -> dict[str, Any]:
    """Search this principal's durable memories only.

    Optional substring filter on content. Ordered by recency (updated_at).
    No application importance/semantic ranking policy.
    """
    pid = _as_uuid(principal_id)
    if not pid:
        return {"ok": False, "error": "no_principal", "memories": []}

    limit = max(1, min(int(limit or 20), 100))
    stmt = select(Memory).where(Memory.principal_id == pid)
    if not include_inactive:
        stmt = stmt.where(Memory.is_active.is_(True))
    if memory_type:
        stmt = stmt.where(Memory.memory_type == str(memory_type)[:50])
    if query and str(query).strip():
        q = f"%{str(query).strip()[:200]}%"
        stmt = stmt.where(Memory.content.ilike(q))
    stmt = stmt.order_by(Memory.updated_at.desc()).limit(limit)
    result = await session.execute(stmt)
    rows = list(result.scalars().all())

    return {
        "ok": True,
        "memories": [_row_to_dict(m) for m in rows],
        "count": len(rows),
        "principal_id": str(pid),
    }


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
    confidence: float = 0.7,
    importance: float = 0.5,
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
        confidence=float(confidence if confidence is not None else 0.7),
        importance=float(importance if importance is not None else 0.5),
        source=str(source or "ai")[:50],
        source_work_id=_as_uuid(work_id),
        is_active=True,
        validity_status="active",
        tags=[str(t)[:80] for t in (tags or [])][:20],
        last_observed_at=datetime.now(timezone.utc),
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
    confidence: float | None = None,
    importance: float | None = None,
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
    if confidence is not None:
        m.confidence = float(confidence)
    if importance is not None:
        m.importance = float(importance)
    if tags is not None:
        m.tags = [str(t)[:80] for t in tags][:20]
    if structured is not None:
        m.structured = dict(structured)
    m.last_confirmed_at = datetime.now(timezone.utc)
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
        confidence=old.confidence,
        importance=old.importance,
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
