"""
Durable work engine — claim, complete, fail, recover.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.config import get_settings
from wax.db.models import Delivery, Execution, Work
from wax.observability.logging import get_logger, work_id_var

logger = get_logger(__name__)
settings = get_settings()


class WorkEngine:
    def __init__(self, session: AsyncSession, worker_id: str = "default"):
        self.session = session
        self.worker_id = worker_id

    async def create(
        self,
        *,
        kind: str,
        principal_id=None,
        conversation_id=None,
        objective: str | None = None,
        input_payload: dict[str, Any] | None = None,
        priority: int = 100,
        max_attempts: int | None = None,
    ) -> Work:
        work = Work(
            id=uuid4(),
            principal_id=principal_id,
            conversation_id=conversation_id,
            kind=kind,
            status="queued",
            priority=priority,
            objective=objective,
            input_payload=input_payload or {},
            max_attempts=max_attempts or settings.work_max_retries,
        )
        self.session.add(work)
        await self.session.flush()
        logger.info("work_created", work_id=str(work.id), kind=kind)
        return work

    async def claim(self, kinds: list[str] | None = None) -> Work | None:
        now = datetime.now(timezone.utc)
        stmt = (
            select(Work)
            .where(
                or_(
                    Work.status == "queued",
                    (Work.status == "retrying")
                    & ((Work.next_retry_at.is_(None)) | (Work.next_retry_at <= now)),
                )
            )
            .order_by(Work.priority.asc(), Work.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if kinds:
            stmt = stmt.where(Work.kind.in_(kinds))
        result = await self.session.execute(stmt)
        work = result.scalar_one_or_none()
        if not work:
            return None
        work.status = "running"
        work.claimed_by = self.worker_id
        work.claimed_at = now
        work.started_at = now
        work.attempt += 1
        await self.session.flush()
        work_id_var.set(str(work.id))
        return work

    async def start_execution(self, work: Work) -> Execution:
        ex = Execution(
            id=uuid4(),
            work_id=work.id,
            status="running",
            started_at=datetime.now(timezone.utc),
            checkpoint={},
            steps=[],
        )
        self.session.add(ex)
        await self.session.flush()
        return ex

    async def complete(self, work: Work, result: dict[str, Any] | None = None) -> None:
        work.status = "completed"
        work.completed_at = datetime.now(timezone.utc)
        work.result_payload = result or {}
        await self.session.flush()

    async def fail(
        self,
        work: Work,
        error: str,
        *,
        error_class: str = "execution_failure",
        retryable: bool = True,
    ) -> None:
        work.error = error
        work.error_class = error_class
        if retryable and work.attempt < work.max_attempts:
            work.status = "retrying"
            delay = min(300, 2 ** work.attempt * 5)
            work.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
        else:
            work.status = "failed"
            work.completed_at = datetime.now(timezone.utc)
        await self.session.flush()

    async def create_delivery(
        self,
        *,
        work_id,
        principal_id,
        channel: str,
        target: str,
        content: str,
        idempotency_key: str | None = None,
    ) -> Delivery:
        key = idempotency_key or f"delivery:{work_id}"
        d = Delivery(
            id=uuid4(),
            work_id=work_id,
            principal_id=principal_id,
            channel=channel,
            target_external_id=target,
            content=content,
            status="pending",
            idempotency_key=key,
        )
        self.session.add(d)
        await self.session.flush()
        return d
