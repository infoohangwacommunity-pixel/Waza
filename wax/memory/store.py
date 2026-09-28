"""
Memory store — durable storage; AI owns relevance and lifecycle.

Infrastructure stores and returns. The AI decides what matters,
what to create/update/supersede/forget, and when to search.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import Memory
from wax.observability.logging import get_logger

logger = get_logger(__name__)


def _row_to_dict(m: Memory) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "content": m.content,
        "memory_type": m.memory_type,
        "confidence": m.confidence,
        "importance": m.importance,
        "is_active": m.is_active,
        "validity_status": getattr(m, "validity_status", None) or "active",
        "superseded_by_id": str(m.superseded_by_id) if m.superseded_by_id else None,
        "tags": list(m.tags or []),
        "structured": dict(m.structured or {}),
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "updated_at": m.updated_at.isoformat() if getattr(m, "updated_at", None) else None,
    }


async def memory_search(
    session: AsyncSession,
    principal_id: Any,
    *,
    query: str | None = None,
    memory_type: str | None = None,
    limit: int = 20,
    include_inactive: bool = False,
) -> dict[str, Any]:
    """Search active memories for this principal. AI decides query depth and use."""
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    limit = max(1, min(int(limit or 20), 50))
    stmt = select(Memory).where(Memory.principal_id == principal_id)
    if not include_inactive:
        stmt = stmt.where(Memory.is_active.is_(True))
        # Prefer not-superseded/forgotten
        stmt = stmt.where(
            or_(
                Memory.validity_status.is_(None),
                Memory.validity_status.in_(["active", "uncertain", "historical"]),
            )
        )
    if memory_type:
        stmt = stmt.where(Memory.memory_type == str(memory_type)[:50])
    if query and query.strip():
        q = f"%{query.strip()[:200]}%"
        stmt = stmt.where(Memory.content.ilike(q))
    stmt = stmt.order_by(Memory.importance.desc(), Memory.created_at.desc()).limit(limit)
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    return {
        "ok": True,
        "count": len(rows),
        "memories": [_row_to_dict(m) for m in rows],
    }


async def memory_get(
    session: AsyncSession, principal_id: Any, memory_id: str
) -> dict[str, Any]:
    if not principal_id or not memory_id:
        return {"ok": False, "error": "principal_and_id_required"}
    m = await session.get(Memory, memory_id)
    if not m or m.principal_id != principal_id:
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
    source: str = "tutor",
    work_id: Any | None = None,
) -> dict[str, Any]:
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    content = (content or "").strip()
    if not content:
        return {"ok": False, "error": "content_required"}
    m = Memory(
        id=uuid4(),
        principal_id=principal_id,
        memory_type=(memory_type or "semantic")[:50],
        content=content[:20000],
        confidence=float(max(0.0, min(confidence, 1.0))),
        importance=float(max(0.0, min(importance, 1.0))),
        source=(source or "tutor")[:50],
        source_work_id=work_id,
        is_active=True,
        validity_status="active",
        tags=list(tags or [])[:20],
        structured=dict(structured or {}),
        last_observed_at=datetime.now(timezone.utc),
    )
    session.add(m)
    await session.flush()
    logger.info("memory_created", memory_id=str(m.id), principal_id=str(principal_id))
    return {"ok": True, "memory": _row_to_dict(m)}


async def memory_update(
    session: AsyncSession,
    principal_id: Any,
    memory_id: str,
    *,
    content: str | None = None,
    confidence: float | None = None,
    importance: float | None = None,
    tags: list[str] | None = None,
    structured: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not principal_id or not memory_id:
        return {"ok": False, "error": "principal_and_id_required"}
    m = await session.get(Memory, memory_id)
    if not m or m.principal_id != principal_id:
        return {"ok": False, "error": "not_found"}
    if content is not None:
        m.content = content.strip()[:20000]
    if confidence is not None:
        m.confidence = float(max(0.0, min(confidence, 1.0)))
    if importance is not None:
        m.importance = float(max(0.0, min(importance, 1.0)))
    if tags is not None:
        m.tags = list(tags)[:20]
    if structured is not None:
        m.structured = dict(structured)
    m.last_confirmed_at = datetime.now(timezone.utc)
    await session.flush()
    return {"ok": True, "memory": _row_to_dict(m)}


async def memory_supersede(
    session: AsyncSession,
    principal_id: Any,
    old_memory_id: str,
    *,
    new_content: str,
    reason: str | None = None,
    memory_type: str | None = None,
    work_id: Any | None = None,
) -> dict[str, Any]:
    """Mark old memory superseded and create replacement. AI decides when history changed."""
    if not principal_id or not old_memory_id:
        return {"ok": False, "error": "principal_and_id_required"}
    old = await session.get(Memory, old_memory_id)
    if not old or old.principal_id != principal_id:
        return {"ok": False, "error": "not_found"}
    new_content = (new_content or "").strip()
    if not new_content:
        return {"ok": False, "error": "new_content_required"}
    created = await memory_create(
        session,
        principal_id,
        content=new_content,
        memory_type=memory_type or old.memory_type,
        confidence=max(old.confidence, 0.7),
        importance=old.importance,
        tags=list(old.tags or []),
        structured={
            **dict(old.structured or {}),
            "supersede_reason": (reason or "")[:500],
            "supersedes": str(old.id),
        },
        source="tutor_supersede",
        work_id=work_id,
    )
    if not created.get("ok"):
        return created
    new_id = created["memory"]["id"]
    old.is_active = False
    old.validity_status = "superseded"
    old.superseded_by_id = new_id
    await session.flush()
    logger.info(
        "memory_superseded",
        old_id=str(old.id),
        new_id=new_id,
        principal_id=str(principal_id),
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
    """Forget by id or by matching query. Real deletion of active status."""
    if not principal_id:
        return {"ok": False, "error": "no_principal"}
    forgotten: list[str] = []
    if memory_id:
        m = await session.get(Memory, memory_id)
        if not m or m.principal_id != principal_id:
            return {"ok": False, "error": "not_found"}
        m.is_active = False
        m.validity_status = "forgotten"
        forgotten.append(str(m.id))
        await session.flush()
        return {"ok": True, "forgotten_ids": forgotten}
    if query and query.strip():
        q = f"%{query.strip()[:200]}%"
        stmt = (
            select(Memory)
            .where(
                Memory.principal_id == principal_id,
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
