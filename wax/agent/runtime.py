"""Agent execution with durable checkpoints.

Continuation is driven by objectives and observations, not a tool-call budget.
Infrastructure safety limits (wall time, max continuations) protect against runaway
processes — they are not an intelligence architecture.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from wax.config import get_settings
from wax.db.models import Execution, Work
from wax.observability.logging import get_logger
from wax.observability.correlation import set_execution_id

logger = get_logger(__name__)

# Infrastructure safety only — not an intelligence "tool budget"
_MAX_CONTINUATIONS = 24  # hard ceiling against infinite agent loops
_MAX_WALL_SECONDS = 180


class AgentRuntime:
    def __init__(self, session: AsyncSession, work: Work):
        self.session = session
        self.work = work
        self.execution: Execution | None = None
        self.settings = get_settings()
        self.continuations = 0
        self.started_at = datetime.now(timezone.utc)

    async def start(self) -> Execution:
        now = datetime.now(timezone.utc)
        meta = dict(self.work.metadata_ or {})
        exec_id = meta.get("execution_id")
        if exec_id:
            from uuid import UUID
            try:
                existing = await self.session.get(Execution, UUID(str(exec_id)))
                if existing and existing.status == "running":
                    self.execution = existing
                    steps = list(existing.steps or [])
                    self.continuations = sum(1 for s in steps if s.get("type") == "continue")
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
        """Infrastructure safety: stop runaway loops / wall time."""
        if self.continuations >= _MAX_CONTINUATIONS:
            return False
        elapsed = (datetime.now(timezone.utc) - self.started_at).total_seconds()
        if elapsed > _MAX_WALL_SECONDS:
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
            self.execution.checkpoint = {**(self.execution.checkpoint or {}), "result_preview": str(result)[:500]}
        await self.session.flush()

    async def fail(self, error: str) -> None:
        if not self.execution:
            return
        self.execution.status = "failed"
        self.execution.completed_at = datetime.now(timezone.utc)
        self.execution.checkpoint = {**(self.execution.checkpoint or {}), "error": error[:1000]}
        await self.session.flush()
