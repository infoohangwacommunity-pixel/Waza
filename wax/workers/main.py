"""WAX Prep background worker — claims durable Work and processes it."""

from __future__ import annotations

import asyncio
import signal
from datetime import datetime, timezone

from sqlalchemy import select, update

from wax.config import get_settings
from wax.db.models import Work
from wax.db.session import session_scope
from wax.observability.logging import get_logger, setup_logging, work_id_var

setup_logging()
logger = get_logger(__name__)
settings = get_settings()
RUNNING = True


def _handle_signal(*_):
    global RUNNING
    RUNNING = False
    logger.info("worker_shutdown_requested")


async def claim_and_process(worker_id: str) -> None:
    async with session_scope() as session:
        stmt = (
            select(Work)
            .where(Work.status == "queued")
            .order_by(Work.priority.asc(), Work.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        result = await session.execute(stmt)
        work = result.scalar_one_or_none()
        if not work:
            return
        work.status = "running"
        work.claimed_by = worker_id
        work.claimed_at = datetime.now(timezone.utc)
        work.started_at = datetime.now(timezone.utc)
        work.attempt += 1
        await session.flush()
        work_id_var.set(str(work.id))
        logger.info("work_claimed", work_id=str(work.id), kind=work.kind)

        # Placeholder: full intelligence + memory + delivery will be wired next
        try:
            work.status = "completed"
            work.completed_at = datetime.now(timezone.utc)
            work.result_payload = {"note": "foundation worker — intelligence loop pending"}
            logger.info("work_completed_placeholder", work_id=str(work.id))
        except Exception as e:
            work.status = "failed"
            work.error = str(e)
            work.error_class = "execution_failure"
            logger.exception("work_failed", work_id=str(work.id))


async def loop(worker_id: str) -> None:
    while RUNNING:
        try:
            await claim_and_process(worker_id)
        except Exception:
            logger.exception("worker_loop_error")
        await asyncio.sleep(settings.work_poll_interval_seconds)


async def main() -> None:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    logger.info("worker_started")
    await asyncio.gather(*[loop(f"w-{i}") for i in range(2)])


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
