"""
Conversational correction / supersession for durable learner facts.

Recognizes goal/preference/fact corrections without hardcoded phrase lists only —
uses planner signals + active memory search + supersede.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from wax.memory.service import MemoryService
from wax.observability.logging import get_logger
from wax.learner.events import record_learner_event
from wax.learner.contradiction import EvidenceView, resolve_conflict

logger = get_logger(__name__)


async def apply_possible_correction(
    session: AsyncSession,
    *,
    principal_id,
    user_text: str,
    new_statement: str | None = None,
) -> dict[str, Any]:
    """
    If user_text looks like a correction, find conflicting active memories and supersede.
    Returns {corrected: bool, details: [...]}
    """
    text = (user_text or "").strip()
    if not text:
        return {"corrected": False, "details": []}
    mem = MemoryService(session)
    # Find active goal/preference/semantic memories that might conflict
    candidates = await mem.retrieve_relevant(
        principal_id,
        text,
        limit=10,
        memory_types=["goal", "preference", "semantic"],
        min_confidence=0.2,
    )
    if not candidates:
        return {"corrected": False, "details": []}

    details = []
    new_content = (new_statement or text)[:500]
    views = [
        EvidenceView(
            id=str(c.id),
            content=c.content or "",
            kind=c.memory_type or "semantic",
            source=c.source or "inferred",
            confidence=float(c.confidence or 0.5),
            recency=0.3,
            is_correction=False,
        )
        for c in candidates
    ]
    views.append(
        EvidenceView(
            id="incoming",
            content=new_content,
            kind="semantic",
            source="learner_correction",
            confidence=0.88,
            recency=1.0,
            is_correction=True,
        )
    )
    resolved = resolve_conflict(views)
    if resolved.get("status") == "uncertain":
        logger.info("correction_uncertain_not_forced", principal_id=str(principal_id))
        return {"corrected": False, "details": [], "status": "uncertain"}
    for old in candidates[:3]:
        # only supersede if types match likely correction domain
        if old.memory_type not in ("goal", "preference", "semantic"):
            continue
        # skip if nearly identical
        if (old.content or "").strip().lower() == new_content.strip().lower():
            continue
        new_mem = await mem.supersede(
            principal_id,
            old.id,
            new_content,
            source="learner_correction",
            confidence=0.88,
        )
        await record_learner_event(
            session,
            principal_id=principal_id,
            kind="preference_corrected" if old.memory_type == "preference" else "correction",
            summary=f"Superseded: {(old.content or '')[:120]} → {new_content[:120]}",
            memory_id=new_mem.id,
            payload={"old_memory_id": str(old.id), "new_memory_id": str(new_mem.id)},
            confidence=0.85,
        )
        details.append(
            {
                "old_id": str(old.id),
                "new_id": str(new_mem.id),
                "old_content": (old.content or "")[:200],
                "new_content": new_content[:200],
                "type": old.memory_type,
            }
        )
        logger.info(
            "learner_correction_applied",
            principal_id=str(principal_id),
            memory_type=old.memory_type,
        )
    return {"corrected": bool(details), "details": details}
