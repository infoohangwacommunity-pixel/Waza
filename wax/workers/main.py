"""
WAX Prep background worker.

Claims durable Work → runs Tutor intelligence → memory → delivery.
Survives crashes. Every learner message goes through AI.
Only pure infrastructure error paths skip the model.
"""

from __future__ import annotations

import asyncio
import signal
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import or_, select

from wax.config import get_settings
from wax.db.models import Delivery, Work
from wax.db.session import session_scope
from wax.intelligence.tutor import TutorService
from wax.observability.logging import get_logger, setup_logging, work_id_var

setup_logging()
logger = get_logger(__name__)
settings = get_settings()
RUNNING = True


def _handle_signal(*_):
    global RUNNING
    RUNNING = False
    logger.info("worker_shutdown_requested")


async def process_message_response(session, work: Work) -> None:
    """Full AI tutor path — every learner message passes through intelligence."""
    tutor = TutorService(session)
    try:
        result = await tutor.handle_message(work)
        work.status = "completed"
        work.completed_at = datetime.now(timezone.utc)
        work.result_payload = {
            "reply_preview": (result.get("reply") or "")[:400],
            "memories_used": result.get("memories_used", 0),
            "tools": result.get("tools") or [],
        }
        await session.flush()
        logger.info(
            "work_completed",
            work_id=str(work.id),
            memories_used=result.get("memories_used"),
        )
        await _attempt_deliveries(session, work.id)
    except Exception as e:
        logger.exception("tutor_turn_failed", work_id=str(work.id))
        work.error = str(e)
        work.error_class = "execution_failure"
        if work.attempt < work.max_attempts:
            work.status = "retrying"
            delay = min(300, 2 ** work.attempt * 5)
            work.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
        else:
            work.status = "failed"
            work.completed_at = datetime.now(timezone.utc)
        await session.flush()


async def _attempt_deliveries(session, work_id) -> None:
    from wax.delivery.senders import deliver as channel_deliver
    stmt = select(Delivery).where(
        Delivery.work_id == work_id, Delivery.status == "pending"
    )
    result = await session.execute(stmt)
    for delivery in result.scalars().all():
        try:
            outcome = await channel_deliver(
                delivery.channel, delivery.target_external_id, delivery.content
            )
            if outcome.get("status") in ("ok", "skipped"):
                delivery.status = "delivered"
                delivery.delivered_at = datetime.now(timezone.utc)
                delivery.external_message_id = f"out-{uuid4().hex[:12]}"
                delivery.metadata_ = {**(delivery.metadata_ or {}), "outcome": outcome}
            else:
                delivery.status = "failed"
                delivery.error = str(outcome)
                delivery.attempt += 1
            logger.info(
                "delivery_attempted",
                delivery_id=str(delivery.id),
                channel=delivery.channel,
                status=delivery.status,
            )
        except Exception as e:
            delivery.status = "failed"
            delivery.error = str(e)
            delivery.attempt += 1
        await session.flush()


async def claim_next(session, worker_id: str) -> Work | None:
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
    result = await session.execute(stmt)
    work = result.scalar_one_or_none()
    if not work:
        return None
    work.status = "running"
    work.claimed_by = worker_id
    work.claimed_at = now
    work.started_at = now
    work.attempt += 1
    await session.flush()
    work_id_var.set(str(work.id))
    logger.info("work_claimed", work_id=str(work.id), kind=work.kind, attempt=work.attempt)
    return work


async def recover_orphans(session) -> int:
    threshold = datetime.now(timezone.utc) - timedelta(seconds=settings.work_stale_seconds)
    stmt = select(Work).where(Work.status == "running", Work.claimed_at < threshold)
    result = await session.execute(stmt)
    count = 0
    for work in result.scalars().all():
        work.status = "retrying"
        work.next_retry_at = datetime.now(timezone.utc)
        count += 1
    if count:
        await session.flush()
        logger.warning("orphaned_works_recovered", count=count)
    return count


async def worker_loop(worker_id: str) -> None:
    logger.info("worker_started", worker_id=worker_id)
    while RUNNING:
        try:
            async with session_scope() as session:
                work = await claim_next(session, worker_id)
                if not work:
                    await asyncio.sleep(settings.work_poll_interval_seconds)
                    continue
                if work.kind == "message_response":
                    await process_message_response(session, work)
                else:
                    work.status = "failed"
                    work.error = f"Unknown work kind: {work.kind}"
                    work.error_class = "configuration_failure"
                    await session.flush()
        except Exception:
            logger.exception("worker_loop_error")
            await asyncio.sleep(2.0)
    logger.info("worker_stopped", worker_id=worker_id)


async def recovery_loop() -> None:
    while RUNNING:
        try:
            async with session_scope() as session:
                await recover_orphans(session)
        except Exception:
            logger.exception("recovery_error")
        await asyncio.sleep(60)


async def main() -> None:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    tasks = [asyncio.create_task(worker_loop(f"worker-{i}")) for i in range(2)]
    tasks.append(asyncio.create_task(recovery_loop()))
    await asyncio.gather(*tasks)


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
