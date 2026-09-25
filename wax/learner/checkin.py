"""
Conversational check-in guardrails.

The AI decides whether a check-in feels natural in the current moment.
Infrastructure decides whether the system is allowed to ask right now.

No crude scheduled surveys. No interruption of focused tutoring.
No forced rating forms.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.config.settings import get_settings
from wax.db.models import Memory, Message
from wax.observability.logging import get_logger

logger = get_logger(__name__)

CHECKIN_TAG = "checkin_asked"


async def checkin_allowed(
    session: AsyncSession,
    *,
    principal_id: UUID,
    conversation_id: UUID | None = None,
    context_hints: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """
    Returns (allowed, reason).
    reason is for internal logs / tutor context, never raw to student.
    """
    settings = get_settings()
    if not getattr(settings, "checkin_enabled", True):
        return False, "disabled"

    min_msgs = int(getattr(settings, "checkin_min_messages", 25) or 25)
    cooldown_h = float(getattr(settings, "checkin_cooldown_hours", 72.0) or 72.0)
    max_week = int(getattr(settings, "checkin_max_per_week", 2) or 2)
    suppress_problem = bool(getattr(settings, "checkin_suppress_on_active_problem", True))
    suppress_after_fb = int(getattr(settings, "checkin_suppress_after_feedback_seconds", 300) or 300)

    hints = context_hints or {}

    msg_count = await session.scalar(
        select(func.count()).select_from(Message).where(
            Message.principal_id == principal_id,
            Message.direction == "inbound",
        )
    )
    if (msg_count or 0) < min_msgs:
        return False, "insufficient_history"

    # Cooldown via tagged Memory (works for all channels; survives restart)
    since = datetime.now(timezone.utc) - timedelta(hours=cooldown_h)
    last = await session.scalar(
        select(func.max(Memory.created_at)).where(
            Memory.principal_id == principal_id,
            Memory.tags.contains([CHECKIN_TAG]),
            Memory.created_at >= since,
        )
    )
    if last:
        return False, "cooldown"

    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    week_count = await session.scalar(
        select(func.count()).select_from(Memory).where(
            Memory.principal_id == principal_id,
            Memory.tags.contains([CHECKIN_TAG]),
            Memory.created_at >= week_ago,
        )
    )
    if (week_count or 0) >= max_week:
        return False, "weekly_cap"

    if suppress_problem and hints.get("active_problem"):
        return False, "active_problem"
    if hints.get("frustrated") or hints.get("waiting_for_answer"):
        return False, "context_suppressed"
    if hints.get("recent_feedback_seconds") is not None:
        if float(hints["recent_feedback_seconds"]) < suppress_after_fb:
            return False, "recent_feedback"

    return True, "eligible"


async def record_checkin_asked(
    session: AsyncSession,
    *,
    principal_id: UUID,
    conversation_id: UUID | None = None,
) -> None:
    """Durable record that a check-in was issued (cooldown accounting)."""
    from wax.db.models import Memory

    try:
        m = Memory(
            principal_id=principal_id,
            memory_type="behavioral",
            content="checkin_asked",
            structured={
                "kind": CHECKIN_TAG,
                "conversation_id": str(conversation_id) if conversation_id else None,
            },
            confidence=1.0,
            importance=0.15,
            source="system",
            evidence=[],
            is_active=True,
            tags=[CHECKIN_TAG],
            last_observed_at=datetime.now(timezone.utc),
        )
        session.add(m)
        await session.flush()
    except Exception:
        logger.exception("record_checkin_asked_failed")
