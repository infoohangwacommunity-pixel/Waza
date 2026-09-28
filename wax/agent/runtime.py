"""
Agent execution bookkeeping.

Continuation is driven by objectives and observations — not tool-call or intelligence budgets.

Infrastructure safety limits below protect against runaway loops and resource exhaustion.
They are NOT application-level caps on what the AI is conceptually allowed to think or do.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import Execution, Work
from wax.observability.logging import get_logger, set_execution_id

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Infrastructure safety boundaries (runaway / DoS / resource exhaustion)
# NOT intelligence budgets. Raise if legitimate long objectives hit them in production.
# ---------------------------------------------------------------------------
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
        now = datetime.now(timezone.utc)
        meta = dict(self.work.metadata_ or {})
        existing_id = meta.get("execution_id")
        if existing_id:
            try:
                from uuid import UUID

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
        """
        Infrastructure safety only.

        Returns False when a runaway loop or wall-clock exhaustion is likely.
        Does not encode "the AI has used enough intelligence."
        """
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

    async def record_continuation(self, kind: str, detail: dict[str, Any] | None = None) -> None:
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
                **(detail or {}),
            }
        )
        self.execution.steps = steps
        self.execution.checkpoint = {
            "phase": "continue",
            "kind": kind,
            "continuations": self.continuations,
        }
        await self.session.flush()

    async def complete(self, result: dict[str, Any] | None = None) -> None:
        if not self.execution:
            return
        self.execution.status = "completed"
        self.execution.completed_at = datetime.now(timezone.utc)
        if result:
            self.execution.checkpoint = {
                **(self.execution.checkpoint or {}),
                "result_preview": str(result)[:500],
            }
        await self.session.flush()

    async def fail(self, error: str) -> None:
        if not self.execution:
            return
        self.execution.status = "failed"
        self.execution.completed_at = datetime.now(timezone.utc)
        self.execution.checkpoint = {
            **(self.execution.checkpoint or {}),
            "error": error[:1000],
        }
        await self.session.flush()
