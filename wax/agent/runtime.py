"""Budgeted agent execution with durable checkpoints.

Replaces a fixed "max 3 tool rounds" mindset with:
- max tool rounds (config)
- step log on Execution
- checkpoint JSON for resume
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from wax.config import get_settings
from wax.db.models import Execution, Work
from wax.observability.logging import get_logger

logger = get_logger(__name__)


class AgentRuntime:
    def __init__(self, session: AsyncSession, work: Work):
        self.session = session
        self.work = work
        self.execution: Execution | None = None
        self.settings = get_settings()
        self.tool_calls = 0
        self.max_rounds = max(1, min(int(getattr(self.settings, "agent_max_tool_rounds", 8) or 8), 20))

    async def start(self) -> Execution:
        now = datetime.now(timezone.utc)
        # Resume existing running execution if present
        meta = dict(self.work.metadata_ or {})
        exec_id = meta.get("execution_id")
        if exec_id:
            from uuid import UUID
            try:
                existing = await self.session.get(Execution, UUID(str(exec_id)))
                if existing and existing.status == "running":
                    self.execution = existing
                    steps = list(existing.steps or [])
                    self.tool_calls = sum(1 for s in steps if s.get("type") == "tool")
                    logger.info(
                        "agent_execution_resumed",
                        work_id=str(self.work.id),
                        execution_id=str(existing.id),
                        tool_calls=self.tool_calls,
                    )
                    return existing
            except Exception:
                pass

        ex = Execution(
            id=uuid4(),
            work_id=self.work.id,
            status="running",
            started_at=now,
            checkpoint={"phase": "started", "at": now.isoformat()},
            steps=[{"type": "start", "at": now.isoformat()}],
        )
        self.session.add(ex)
        await self.session.flush()
        meta["execution_id"] = str(ex.id)
        self.work.metadata_ = meta
        await self.session.flush()
        self.execution = ex
        logger.info(
            "agent_execution_started",
            work_id=str(self.work.id),
            execution_id=str(ex.id),
            max_rounds=self.max_rounds,
        )
        return ex

    def can_call_tool(self) -> bool:
        return self.tool_calls < self.max_rounds

    async def record_tool(
        self, name: str, args: dict[str, Any], outcome: dict[str, Any]
    ) -> None:
        if not self.execution:
            return
        self.tool_calls += 1
        now = datetime.now(timezone.utc).isoformat()
        steps = list(self.execution.steps or [])
        steps.append(
            {
                "type": "tool",
                "name": name,
                "ok": bool(outcome.get("ok")),
                "at": now,
                "n": self.tool_calls,
                # Safe summary only — no secrets
                "summary": str(outcome)[:400],
            }
        )
        self.execution.steps = steps
        self.execution.checkpoint = {
            "phase": "tool",
            "last_tool": name,
            "tool_calls": self.tool_calls,
            "at": now,
        }
        await self.session.flush()
        logger.info(
            "agent_tool_step",
            work_id=str(self.work.id),
            execution_id=str(self.execution.id),
            tool=name,
            ok=bool(outcome.get("ok")),
            n=self.tool_calls,
        )

    async def complete(self, *, reply_preview: str | None = None) -> None:
        if not self.execution:
            return
        now = datetime.now(timezone.utc)
        self.execution.status = "completed"
        self.execution.completed_at = now
        self.execution.checkpoint = {
            "phase": "completed",
            "at": now.isoformat(),
            "tool_calls": self.tool_calls,
            "reply_preview": (reply_preview or "")[:200],
        }
        steps = list(self.execution.steps or [])
        steps.append({"type": "complete", "at": now.isoformat()})
        self.execution.steps = steps
        await self.session.flush()
        logger.info(
            "agent_execution_completed",
            work_id=str(self.work.id),
            execution_id=str(self.execution.id),
            tool_calls=self.tool_calls,
        )

    async def fail(self, error: str, error_class: str = "agent_failure") -> None:
        if not self.execution:
            return
        now = datetime.now(timezone.utc)
        self.execution.status = "failed"
        self.execution.completed_at = now
        self.execution.error = error[:2000]
        self.execution.error_class = error_class
        self.execution.checkpoint = {
            "phase": "failed",
            "at": now.isoformat(),
            "error_class": error_class,
        }
        await self.session.flush()
