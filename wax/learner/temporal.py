"""
Temporal Intelligence — resolve natural language time; schedule intents not wording.

Authoritative clock + learner timezone. Scheduler wakes tutor to reassess.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import ScheduledAction, TemporalIntent
from wax.observability.logging import get_logger
from wax.scheduler.service import SchedulerService

logger = get_logger(__name__)


def resolve_timezone(name: str | None) -> ZoneInfo:
    if not name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def now_in_tz(tz_name: str | None = None) -> datetime:
    return datetime.now(tz=resolve_timezone(tz_name))


def resolve_natural_time(
    text: str,
    *,
    reference: datetime | None = None,
    tz_name: str = "UTC",
) -> dict[str, Any] | None:
    """
    Deterministic resolution of common relative expressions.
    Returns absolute execute_at in UTC + local iso. Never trusts LLM arithmetic.
    """
    tz = resolve_timezone(tz_name)
    ref = reference or datetime.now(tz=tz)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=tz)
    else:
        ref = ref.astimezone(tz)
    t = (text or "").strip().lower()

    # tomorrow at HH[:MM][am/pm]
    m = re.search(
        r"\b(?:tomorrow|tmr|tmrw)\b(?:\s+at)?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?",
        t,
    )
    if m or re.search(r"\btomorrow\b", t):
        day = (ref + timedelta(days=1)).date()
        hour, minute = 9, 0
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2) or 0)
            ap = m.group(3)
            if ap == "pm" and hour < 12:
                hour += 12
            if ap == "am" and hour == 12:
                hour = 0
        elif re.search(r"\bmorning\b", t):
            hour = 9
        elif re.search(r"\bevening\b", t):
            hour = 18
        local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
        return {
            "local": local.isoformat(),
            "utc": local.astimezone(timezone.utc).isoformat(),
            "execute_at": local.astimezone(timezone.utc),
            "timezone": str(tz),
            "flexibility": "hard" if m else "soft",
        }

    # in N hours / minutes
    m = re.search(r"\bin\s+(\d+)\s*(hours?|hrs?|minutes?|mins?)\b", t)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        delta = timedelta(hours=n) if unit.startswith("h") else timedelta(minutes=n)
        local = ref + delta
        return {
            "local": local.isoformat(),
            "utc": local.astimezone(timezone.utc).isoformat(),
            "execute_at": local.astimezone(timezone.utc),
            "timezone": str(tz),
            "flexibility": "hard",
        }

    # at HH:MM today or with am/pm
    m = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", t)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ap = m.group(3)
        if ap == "pm" and hour < 12:
            hour += 12
        if ap == "am" and hour == 12:
            hour = 0
        local = ref.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if local <= ref:
            local = local + timedelta(days=1)
        return {
            "local": local.isoformat(),
            "utc": local.astimezone(timezone.utc).isoformat(),
            "execute_at": local.astimezone(timezone.utc),
            "timezone": str(tz),
            "flexibility": "hard",
        }

    # next monday etc
    weekdays = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    for name, wd in weekdays.items():
        if re.search(rf"\bnext\s+{name}\b", t) or re.search(rf"\bon\s+{name}\b", t):
            days_ahead = (wd - ref.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            day = (ref + timedelta(days=days_ahead)).date()
            hour = 9
            hm = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", t)
            if hm:
                hour = int(hm.group(1))
                if hm.group(3) == "pm" and hour < 12:
                    hour += 12
            local = datetime(day.year, day.month, day.day, hour, 0, tzinfo=tz)
            return {
                "local": local.isoformat(),
                "utc": local.astimezone(timezone.utc).isoformat(),
                "execute_at": local.astimezone(timezone.utc),
                "timezone": str(tz),
                "flexibility": "soft",
            }

    return None


class TemporalService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.scheduler = SchedulerService(session)

    async def create_intent(
        self,
        *,
        principal_id,
        purpose: str,
        target: str,
        execute_at: datetime,
        timezone_name: str = "UTC",
        original_request: str | None = None,
        flexibility: str = "hard",
        completion_condition: str | None = None,
        concept_key: str | None = None,
        goal_id=None,
        payload: dict[str, Any] | None = None,
    ) -> TemporalIntent:
        if execute_at.tzinfo is None:
            execute_at = execute_at.replace(tzinfo=timezone.utc)
        key = f"intent:{principal_id}:{purpose}:{execute_at.isoformat()}:{uuid.uuid4().hex[:8]}"
        # underlying ScheduledAction for worker wake
        action = await self.scheduler.schedule_at(
            principal_id=principal_id,
            action_type="temporal_intent",
            execute_at=execute_at,
            reason=target[:500],
            payload={
                "purpose": purpose,
                "target": target,
                "timezone": timezone_name,
                "flexibility": flexibility,
                **(payload or {}),
            },
            idempotency_key=key,
        )
        intent = TemporalIntent(
            principal_id=principal_id,
            purpose=purpose[:80],
            target=target[:4000],
            execute_at=execute_at,
            timezone=timezone_name[:80],
            status="scheduled",
            flexibility=flexibility[:40],
            completion_condition=completion_condition,
            original_request=original_request,
            payload=payload or {},
            scheduled_action_id=action.id if action else None,
            concept_key=concept_key,
            goal_id=goal_id,
            idempotency_key=key,
        )
        self.session.add(intent)
        await self.session.flush()
        from wax.learner.events import record_learner_event

        await record_learner_event(
            self.session,
            principal_id=principal_id,
            kind="reminder_requested" if purpose == "reminder" else "schedule_created",
            summary=f"{purpose}: {target[:200]}",
            scheduled_action_id=action.id if action else None,
            payload={"execute_at": execute_at.isoformat(), "timezone": timezone_name},
        )
        return intent

    async def evaluate_due_intent(self, intent: TemporalIntent, learner_snapshot: dict[str, Any]) -> dict[str, Any]:
        """
        Reassess intent at wake time. Does NOT compose the final message —
        returns a decision for the tutor: deliver | suppress | reschedule | fulfilled.
        """
        now = datetime.now(timezone.utc)
        intent.last_evaluated_at = now
        active = (learner_snapshot.get("activities") or [{}])[0] if learner_snapshot.get("activities") else None
        decision = "deliver"
        reason = "due"
        # completion heuristics: target keywords seen in recent goals/activity completion
        target_l = (intent.target or "").lower()
        if intent.completion_condition and "completed" in (intent.payload or {}):
            decision = "fulfilled"
            reason = "completion_condition_met"
        # if actively in deep study and flexibility soft, suggest defer
        if active and active.get("status") == "active" and intent.flexibility in ("soft", "window"):
            if intent.purpose == "reminder" and not any(
                k in target_l for k in ("exam", "deadline", "submit", "due")
            ):
                decision = "reschedule"
                reason = "learner_in_active_study"
        if intent.purpose == "review":
            # if concept already practiced today, suppress duplicate
            recent = learner_snapshot.get("recent_events") or []
            if any(
                e.get("concept_key") == intent.concept_key and e.get("kind") in ("learning_success", "concept_practiced")
                for e in recent
            ):
                decision = "suppress"
                reason = "already_practiced_recently"

        intent.evaluation = {"decision": decision, "reason": reason, "at": now.isoformat()}
        if decision == "fulfilled":
            intent.status = "completed"
            intent.fulfilled_at = now
        elif decision == "suppress":
            intent.status = "suppressed"
        elif decision == "reschedule":
            intent.status = "rescheduled"
        else:
            intent.status = "due"
        await self.session.flush()
        return {"decision": decision, "reason": reason, "intent_id": str(intent.id), "target": intent.target, "purpose": intent.purpose}

    async def get_intent_for_action(self, action: ScheduledAction) -> TemporalIntent | None:
        stmt = select(TemporalIntent).where(TemporalIntent.scheduled_action_id == action.id)
        return (await self.session.execute(stmt)).scalar_one_or_none()
