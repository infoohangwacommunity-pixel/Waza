"""Post-turn continuity — durable teaching state without keyword triggers."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from wax.learner.events import record_learner_event
from wax.learner.teaching import update_from_turn
from wax.observability.logging import get_logger

logger = get_logger(__name__)


def _is_meaningful(user_text: str, reply_text: str, tool_notes: list[str] | None) -> bool:
    """Structure-based signal only — no keyword / phrase dictionaries."""
    u = (user_text or "").strip()
    if not u:
        return False
    if tool_notes:
        return True
    if len(u) >= 40 or len(reply_text or "") >= 200:
        return True
    if "?" in u and len(u) >= 12:
        return True
    if len(u) >= 12 and len(reply_text or "") >= 80:
        return True
    return False


async def maybe_update_teaching_after_turn(
    session: AsyncSession,
    *,
    principal_id,
    user_text: str,
    reply_text: str,
    tool_notes: list[str] | None = None,
) -> dict[str, Any]:
    """
    Update teaching state from turn outcomes.

    Topic is taken from the user text as a free-form hint when the turn is
    substantive — not from a phrase list. Tool activity is authoritative.
    """
    if not _is_meaningful(user_text, reply_text, tool_notes):
        return {"updated": False, "reason": "trivial"}

    topic_hint = None
    u = (user_text or "").strip()
    # Free-form topic: first line of a substantive user turn (no prefix strip lists)
    if len(u) >= 24:
        topic_hint = u.split("\n")[0][:200]

    planned = None
    if reply_text and len(reply_text) > 80:
        planned = "continue from last explanation"

    await update_from_turn(
        session,
        principal_id,
        topic=topic_hint,
        explained=(reply_text[:180] if reply_text and len(reply_text) > 60 else None),
        planned_next=planned,
        last_move="post_turn",
    )
    await record_learner_event(
        session,
        principal_id=principal_id,
        kind="concept_practiced" if topic_hint else "session_reentered",
        summary=(topic_hint or user_text)[:200],
        confidence=0.55,
    )
    logger.info(
        "teaching_state_post_turn",
        principal_id=str(principal_id),
        topic=topic_hint,
    )
    return {"updated": True, "reason": "post_turn", "topic": topic_hint}
