"""
Durable activities — assessments, practice, long sessions.

General mechanism. Not QuizMode. Survives disconnect and restart.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import Activity
from wax.observability.logging import get_logger

logger = get_logger(__name__)


class ActivityService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def start(
        self,
        *,
        principal_id,
        kind: str,
        objective: str | None = None,
        duration_seconds: int | None = None,
        content: dict[str, Any] | None = None,
        conversation_id=None,
        work_id=None,
    ) -> Activity:
        now = datetime.now(timezone.utc)
        activity = Activity(
            id=uuid4(),
            principal_id=principal_id,
            conversation_id=conversation_id,
            work_id=work_id,
            kind=kind,
            objective=objective,
            status="active",
            started_at=now,
            expected_duration_seconds=duration_seconds,
            ends_at=(now + timedelta(seconds=duration_seconds)) if duration_seconds else None,
            content=content or {},
            progress={"step": 0},
            outcome={},
            evidence=[],
        )
        self.session.add(activity)
        await self.session.flush()
        logger.info("activity_started", activity_id=str(activity.id), kind=kind)
        return activity

    async def update_progress(
        self, activity_id, progress: dict[str, Any], evidence_item: dict[str, Any] | None = None
    ) -> Activity | None:
        activity = await self.session.get(Activity, activity_id)
        if not activity:
            return None
        activity.progress = {**(activity.progress or {}), **progress}
        if evidence_item:
            activity.evidence = list(activity.evidence or []) + [evidence_item]
        await self.session.flush()
        return activity

    async def complete(
        self, activity_id, outcome: dict[str, Any] | None = None
    ) -> Activity | None:
        activity = await self.session.get(Activity, activity_id)
        if not activity:
            return None
        activity.status = "completed"
        activity.completed_at = datetime.now(timezone.utc)
        activity.outcome = outcome or {}
        await self.session.flush()
        return activity

    async def active_for(self, principal_id, limit: int = 5) -> list[Activity]:
        stmt = (
            select(Activity)
            .where(
                Activity.principal_id == principal_id,
                Activity.status.in_(["active", "waiting", "paused"]),
            )
            .order_by(Activity.updated_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


    async def pause(self, activity_id, *, reason: str | None = None):
        from uuid import UUID
        act = await self.session.get(Activity, activity_id if not isinstance(activity_id, str) else UUID(str(activity_id)))
        if not act:
            return None
        if act.status not in ("active", "waiting"):
            return act
        act.status = "paused"
        progress = dict(act.progress or {})
        progress["paused_reason"] = reason
        from datetime import datetime, timezone
        progress["paused_at"] = datetime.now(timezone.utc).isoformat()
        act.progress = progress
        await self.session.flush()
        return act

    async def resume(self, activity_id):
        from uuid import UUID
        act = await self.session.get(Activity, activity_id if not isinstance(activity_id, str) else UUID(str(activity_id)))
        if not act:
            return None
        if act.status != "paused":
            return act
        act.status = "active"
        progress = dict(act.progress or {})
        from datetime import datetime, timezone
        progress["resumed_at"] = datetime.now(timezone.utc).isoformat()
        act.progress = progress
        await self.session.flush()
        return act
