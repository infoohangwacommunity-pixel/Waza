"""
Execution bookkeeping for one Work item.

Resume prior Execution when present. Bound runaway loops and wall-clock.
Not an application agent framework — no tools, plans, or teaching policy.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import Execution, Work
from wax.observability.logging import get_logger, set_execution_id

logger = get_logger(__name__)

# Infrastructure safety only — never sent to the model as teaching policy.
_SAFETY_MAX_CONTINUATIONS = 48
_SAFETY_MAX_WALL_SECONDS = 300


class AgentRuntime:
    def __init__(self, session: AsyncSession, work: Work):
        self.session = session
        self.work = work
        self.execution: Execution | None = None
        self.continuations = 0
        self.started_at = datetime.now(timezone.utc)

    async def start(self) -> Execution:
        meta = dict(self.work.metadata_ or {})
        existing_id = meta.get("execution_id")
        if existing_id:
            try:
                existing = await self.session.get(Execution, UUID(str(existing_id)))
                if existing and existing.work_id == self.work.id:
                    self.execution = existing
                    steps = list(existing.steps or [])
                    self.continuations = sum(1 for s in steps if s.get("type") == "continue")
                    if existing.started_at:
                        self.started_at = existing.started_at
                    set_execution_id(str(existing.id))
                    return existing
            except Exception:
                pass

        now = datetime.now(timezone.utc)
        ex = Execution(
            id=uuid4(),
            work_id=self.work.id,
            status="running",
            started_at=now,
            steps=[],
            checkpoint={},
        )
        self.session.add(ex)
        await self.session.flush()
        self.execution = ex
        meta["execution_id"] = str(ex.id)
        self.work.metadata_ = meta
        set_execution_id(str(ex.id))
        return ex

    def can_continue(self) -> bool:
        """False only when runaway loop or wall-clock exhaustion is likely."""
        if self.continuations >= _SAFETY_MAX_CONTINUATIONS:
            logger.warning(
                "agent_safety_continuation_ceiling",
                work_id=str(self.work.id),
                continuations=self.continuations,
            )
            return False
        elapsed = (datetime.now(timezone.utc) - self.started_at).total_seconds()
        if elapsed > _SAFETY_MAX_WALL_SECONDS:
            logger.warning(
                "agent_safety_wall_time",
                work_id=str(self.work.id),
                elapsed=round(elapsed, 1),
            )
            return False
        return True

    async def tick(self, kind: str = "continue") -> None:
        self.continuations += 1
        if not self.execution:
            return
        steps = list(self.execution.steps or [])
        steps.append(
            {
                "type": "continue",
                "kind": kind,
                "n": self.continuations,
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self.execution.steps = steps
        await self.session.flush()

    async def complete(self, preview: str = "") -> None:
        if not self.execution:
            return
        self.execution.status = "completed"
        self.execution.completed_at = datetime.now(timezone.utc)
        if preview:
            self.execution.checkpoint = {"result_preview": preview[:500]}
        await self.session.flush()

    async def fail(self, error: str) -> None:
        if not self.execution:
            return
        self.execution.status = "failed"
        self.execution.completed_at = datetime.now(timezone.utc)
        self.execution.checkpoint = {"error": (error or "")[:1000]}
        await self.session.flush()
