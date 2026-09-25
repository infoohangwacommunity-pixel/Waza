"""
Conversational check-in guardrails.

AI decides whether a check-in feels natural.
Infrastructure decides whether the system is allowed to ask.

Atomic accounting uses principal_workloads with row lock so two
workers cannot both issue a check-in in the same window.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from wax.config.settings import get_settings
from wax.observability.logging import get_logger

logger = get_logger(__name__)

CHECKIN_TAG = "checkin_asked"


async def checkin_allowed(
    session,
    *,
    principal_id: UUID,
    conversation_id: UUID | None = None,
    context_hints: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    settings = get_settings()
    if not getattr(settings, "checkin_enabled", True):
        return False, "disabled"

    min_msgs = int(getattr(settings, "checkin_min_messages", 25) or 25)
    cooldown_h = float(getattr(settings, "checkin_cooldown_hours", 72.0) or 72.0)
    max_week = int(getattr(settings, "checkin_max_per_week", 2) or 2)
    suppress_problem = bool(getattr(settings, "checkin_suppress_on_active_problem", True))
    suppress_after_fb = int(getattr(settings, "checkin_suppress_after_feedback_seconds", 300) or 300)
    hints = context_hints or {}

    from sqlalchemy import func, select
    from wax.db.models import Message, PrincipalWorkload

    msg_count = await session.scalar(
        select(func.count()).select_from(Message).where(
            Message.principal_id == principal_id,
            Message.direction == "inbound",
        )
    )
    if (msg_count or 0) < min_msgs:
        return False, "insufficient_history"

    if suppress_problem and hints.get("active_problem"):
        return False, "active_problem"
    if hints.get("frustrated") or hints.get("waiting_for_answer"):
        return False, "context_suppressed"
    if hints.get("recent_feedback_seconds") is not None:
        if float(hints["recent_feedback_seconds"]) < suppress_after_fb:
            return False, "recent_feedback"

    now = datetime.now(timezone.utc)
    row = await session.scalar(
        select(PrincipalWorkload)
        .where(PrincipalWorkload.principal_id == principal_id)
        .with_for_update()
    )
    if row is None:
        return True, "eligible"

    if row.last_checkin_at and (now - row.last_checkin_at) < timedelta(hours=cooldown_h):
        return False, "cooldown"

    week_start = row.checkin_week_start
    if not week_start or (now - week_start) >= timedelta(days=7):
        return True, "eligible"
    if int(row.checkin_week_count or 0) >= max_week:
        return False, "weekly_cap"
    return True, "eligible"


async def claim_checkin(session, *, principal_id: UUID) -> bool:
    """
    Atomically claim a check-in slot. Returns False if another worker won the race.
    """
    settings = get_settings()
    cooldown_h = float(getattr(settings, "checkin_cooldown_hours", 72.0) or 72.0)
    max_week = int(getattr(settings, "checkin_max_per_week", 2) or 2)
    now = datetime.now(timezone.utc)

    from sqlalchemy import select
    from wax.db.models import PrincipalWorkload

    row = await session.scalar(
        select(PrincipalWorkload)
        .where(PrincipalWorkload.principal_id == principal_id)
        .with_for_update()
    )
    if row is None:
        row = PrincipalWorkload(id=uuid4(), principal_id=principal_id, tokens=12.0)
        session.add(row)
        await session.flush()

    if row.last_checkin_at and (now - row.last_checkin_at) < timedelta(hours=cooldown_h):
        return False
    if row.checkin_week_start and (now - row.checkin_week_start) < timedelta(days=7):
        if int(row.checkin_week_count or 0) >= max_week:
            return False
        row.checkin_week_count = int(row.checkin_week_count or 0) + 1
    else:
        row.checkin_week_start = now
        row.checkin_week_count = 1
    row.last_checkin_at = now
    await session.flush()
    return True


async def record_checkin_asked(
    session,
    *,
    principal_id: UUID,
    conversation_id: UUID | None = None,
) -> None:
    """Durable record + atomic claim."""
    try:
        claimed = await claim_checkin(session, principal_id=principal_id)
        if not claimed:
            logger.info("checkin_claim_lost", principal_id=str(principal_id))
            return
        from wax.db.models import Memory

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
