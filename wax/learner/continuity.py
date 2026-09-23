"""Post-turn continuity — durable teaching state without updating on trivial messages."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from wax.learner.events import record_learner_event
from wax.learner.teaching import update_from_turn, load_teaching_state, branch_thread
from wax.observability.logging import get_logger

logger = get_logger(__name__)

_TRIVIAL = re.compile(
    r"^(hi|hello|hey|thanks|thank you|ok|okay|yes|no|lol|haha|good morning|good night)[.!]?$",
    re.I,
)


def _is_meaningful(user_text: str, reply_text: str, tool_notes: list[str] | None) -> bool:
    u = (user_text or "").strip()
    if not u or _TRIVIAL.match(u):
        return False
    if len(u) < 8 and not (tool_notes):
        return False
    # learning-ish signals
    low = u.lower()
    if any(
        k in low
        for k in (
            "teach",
            "explain",
            "continue",
            "what is",
            "why",
            "how do",
            "help me",
            "understand",
            "practice",
            "revise",
            "remind",
        )
    ):
        return True
    if tool_notes and any(
        n.startswith(
            (
                "update_concept",
                "record_evidence",
                "start_activity",
                "schedule_",
                "world_",
            )
        )
        for n in tool_notes
    ):
        return True
    if len(u) >= 40 or len(reply_text or "") >= 200:
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
    if not _is_meaningful(user_text, reply_text, tool_notes):
        return {"updated": False, "reason": "trivial"}

    low = (user_text or "").lower()
    # Branch on explicit topic switch-ish requests while a thread is active
    if any(k in low for k in ("wait, what is", "actually explain", "switch to", "instead teach")):
        # extract a short topic guess after keywords
        topic = user_text.strip()[:120]
        try:
            await branch_thread(session, principal_id, new_topic=topic, reason="learner_interruption")
            await record_learner_event(
                session,
                principal_id=principal_id,
                kind="activity_started",
                summary=f"Branched teaching thread: {topic[:160]}",
                confidence=0.7,
            )
            return {"updated": True, "reason": "branched"}
        except Exception:
            logger.exception("branch_failed")

    # Continue signal — ensure active thread is resumed in state
    if low.strip() in ("continue", "continue.", "let's continue", "lets continue", "go on"):
        ts = await load_teaching_state(session, principal_id)
        if ts and ts.status == "paused":
            ts.status = "active"
            ts.last_move = "resumed_on_continue"
            from wax.learner.teaching import save_teaching_state

            await save_teaching_state(session, principal_id, ts)
            await record_learner_event(
                session,
                principal_id=principal_id,
                kind="session_reentered",
                summary=f"Resumed: {ts.topic[:160]}",
                confidence=0.85,
            )
            return {"updated": True, "reason": "resumed"}

    # Generic teach turn: set topic from user text if no strong topic
    topic_hint = None
    for prefix in ("teach me ", "explain ", "what is ", "help me with "):
        if low.startswith(prefix):
            topic_hint = user_text[len(prefix) :].strip()[:200]
            break
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
    logger.info("teaching_state_post_turn", principal_id=str(principal_id), topic=topic_hint)
    return {"updated": True, "reason": "post_turn", "topic": topic_hint}
