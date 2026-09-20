"""
Durable scheduler — general mechanism, not educational hardcoding.

The tutor decides *whether* and *why* to schedule.
This service only persists and later wakes the system.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import ScheduledAction, Work
from wax.observability.logging import get_logger

logger = get_logger(__name__)


class SchedulerService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def schedule(
        self,
        *,
        principal_id,
        action_type: str,
        execute_at: datetime,
        reason: str | None = None,
        payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> ScheduledAction:
        key = idempotency_key or f"{principal_id}:{action_type}:{execute_at.isoformat()}:{uuid4().hex[:8]}"
        action = ScheduledAction(
            id=uuid4(),
            principal_id=principal_id,
            action_type=action_type,
            reason=reason,
            execute_at=execute_at,
            status="pending",
            payload=payload or {},
            idempotency_key=key,
        )
        self.session.add(action)
        await self.session.flush()
        logger.info(
            "action_scheduled",
            action_id=str(action.id),
            action_type=action_type,
            execute_at=execute_at.isoformat(),
        )
        return action

    async def schedule_at(
        self,
        *,
        principal_id,
        action_type: str,
        execute_at: datetime,
        reason: str | None = None,
        payload: dict[str, Any] | None = None,
        timezone_name: str | None = None,
        idempotency_key: str | None = None,
    ) -> ScheduledAction:
        """Schedule at an absolute UTC datetime (optional timezone metadata)."""
        if execute_at.tzinfo is None:
            execute_at = execute_at.replace(tzinfo=timezone.utc)
        else:
            execute_at = execute_at.astimezone(timezone.utc)
        pl = dict(payload or {})
        if timezone_name:
            pl["timezone"] = timezone_name
            pl["utc_execute_at"] = execute_at.isoformat()
        return await self.schedule(
            principal_id=principal_id,
            action_type=action_type,
            execute_at=execute_at,
            reason=reason,
            payload=pl,
            idempotency_key=idempotency_key,
        )

    async def schedule_in_hours(
        self,
        *,
        principal_id,
        action_type: str,
        hours: float,
        reason: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> ScheduledAction:
        when = datetime.now(timezone.utc) + timedelta(hours=max(0.01, hours))
        return await self.schedule(
            principal_id=principal_id,
            action_type=action_type,
            execute_at=when,
            reason=reason,
            payload=payload,
        )

    async def due_actions(self, limit: int = 20) -> list[ScheduledAction]:
        now = datetime.now(timezone.utc)
        stmt = (
            select(ScheduledAction)
            .where(
                ScheduledAction.status == "pending",
                ScheduledAction.execute_at <= now,
            )
            .order_by(ScheduledAction.execute_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def mark_executing(self, action: ScheduledAction) -> None:
        action.status = "executing"
        await self.session.flush()

    async def complete(self, action: ScheduledAction, result: dict[str, Any] | None = None) -> None:
        action.status = "completed"
        action.executed_at = datetime.now(timezone.utc)
        action.result = result or {}
        await self.session.flush()

    async def fail(self, action: ScheduledAction, error: str) -> None:
        action.status = "failed"
        action.error = error
        action.executed_at = datetime.now(timezone.utc)
        await self.session.flush()

    async def create_work_for_action(self, action: ScheduledAction) -> Work:
        """Turn a due scheduled action into durable Work for the tutor/worker.

        Invariant: at most one Work per action. Flush Work before setting
        action.work_id so the FK target exists (autoflush=False).
        """
        if action.work_id is not None:
            existing = await self.session.get(Work, action.work_id)
            if existing is not None:
                logger.info(
                    "scheduled_work_reused",
                    action_id=str(action.id),
                    work_id=str(existing.id),
                )
                return existing
            # Stale work_id pointing at missing row — clear and recreate
            logger.warning(
                "scheduled_work_stale_cleared",
                action_id=str(action.id),
                stale_work_id=str(action.work_id),
            )
            action.work_id = None
            await self.session.flush()

        work = Work(
            id=uuid4(),
            principal_id=action.principal_id,
            kind="scheduled_action",
            status="queued",
            priority=80,
            objective=action.reason or action.action_type,
            input_payload={
                "scheduled_action_id": str(action.id),
                "action_type": action.action_type,
                "reason": action.reason,
                "payload": action.payload or {},
            },
        )
        self.session.add(work)
        await self.session.flush()  # works row must exist before FK assign
        action.work_id = work.id
        await self.session.flush()
        logger.info(
            "scheduled_work_created",
            action_id=str(action.id),
            work_id=str(work.id),
        )
        return work
