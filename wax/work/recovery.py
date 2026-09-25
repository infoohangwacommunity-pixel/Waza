"""
Durable recovery: orphan messages, recovery batching, safe outage context.

Principles:
  - Receiving and processing are separate.
  - Accepted messages are never discarded.
  - Original messages remain individual durable records.
  - Batching is a processing representation only.
  - Outage context is safe structured metadata for the tutor — never internals.
  - Recovery is idempotent (Work uniqueness / status transitions).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from wax.config.settings import get_settings
from wax.observability.logging import get_logger

logger = get_logger(__name__)


def build_outage_context(
    *,
    wait_seconds: float,
    preserved_message_count: int,
    recovered: bool = True,
) -> dict[str, Any]:
    """
    Safe structured context for the tutor. No stack traces, URLs, keys, or hostnames.
    """
    settings = get_settings()
    min_s = float(getattr(settings, "outage_awareness_min_seconds", 30) or 30)
    if not recovered or wait_seconds < min_s:
        return {}
    # Bound duration so it stays approximate and safe
    approx = int(min(max(0, wait_seconds), 86400))
    return {
        "recovered_after_interruption": True,
        "approximate_wait_seconds": approx,
        "preserved_message_count": int(max(1, preserved_message_count)),
        "messages_waited_for_recovery": True,
    }


async def recover_orphan_messages(session, limit: int = 20) -> int:
    """
    Find inbound messages that were persisted but never linked to a successful Work,
    and create recovery Work items so they are not permanently invisible.

    Defensive: prefer transactional Message+Work creation in handlers; this is a safety net.
    """

    from sqlalchemy import select
    from wax.db.models import Message, Work
    settings = get_settings()
    # Messages older than a short grace period without work_id
    grace = int(getattr(settings, "work_stale_seconds", 300) or 300)
    threshold = datetime.now(timezone.utc) - timedelta(seconds=min(grace, 120))

    stmt = (
        select(Message)
        .where(
            Message.direction == "inbound",
            Message.work_id.is_(None),
            Message.created_at < threshold,
        )
        .order_by(Message.created_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    result = await session.execute(stmt)
    messages = list(result.scalars().all())
    if not messages:
        return 0

    created = 0
    for msg in messages:
        # Idempotent: skip if a recovery work already exists for this message
        existing = await session.scalar(
            select(Work.id).where(
                Work.kind == "message_response",
                Work.input_payload["message_id"].as_string() == str(msg.id),
            ).limit(1)
        )
        # Fallback: also check metadata
        if existing:
            msg.work_id = existing
            continue

        # Grouping for batch is handled separately; here one work per orphan message
        # (batching merges processing context without deleting messages)
        wait_s = (datetime.now(timezone.utc) - (msg.created_at or datetime.now(timezone.utc))).total_seconds()
        outage = build_outage_context(wait_seconds=wait_s, preserved_message_count=1)

        work = Work(
            id=uuid4(),
            principal_id=msg.principal_id,
            conversation_id=msg.conversation_id,
            kind="message_response",
            status="queued",
            priority=40,  # slightly elevated for recovery
            objective="Recover orphan inbound message",
            input_payload={
                "channel": msg.channel,
                "message_id": str(msg.id),
                "text": msg.content,
                "target_external_id": (msg.metadata_ or {}).get("target_external_id")
                or (msg.metadata_ or {}).get("wa_id")
                or (msg.metadata_ or {}).get("chat_id"),
                "content_type": msg.content_type or "text",
                "recovery": True,
                "outage_context": outage or None,
            },
            metadata_={"recovery": True, "orphan_message": True},
        )
        session.add(work)
        await session.flush()
        msg.work_id = work.id
        created += 1
        logger.warning(
            "orphan_message_recovered",
            message_id=str(msg.id),
            work_id=str(work.id),
            principal_id=str(msg.principal_id),
        )

    if created:
        await session.flush()
    return created


async def find_recovery_batch_candidates(
    session,
    *,
    principal_id: UUID,
    conversation_id: UUID,
    anchor_time: datetime,
) -> list:
    """
    Find nearby unprocessed inbound messages for the same principal+conversation
    within the recovery window. Original messages are never deleted or merged in DB.
    """

    from sqlalchemy import select
    from wax.db.models import Message
    settings = get_settings()
    window = int(getattr(settings, "recovery_batch_window_seconds", 90) or 90)
    max_n = int(getattr(settings, "recovery_max_batch_size", 8) or 8)
    start = anchor_time - timedelta(seconds=window)
    end = anchor_time + timedelta(seconds=window)

    stmt = (
        select(Message)
        .where(
            Message.principal_id == principal_id,
            Message.conversation_id == conversation_id,
            Message.direction == "inbound",
            Message.created_at >= start,
            Message.created_at <= end,
        )
        .order_by(Message.created_at.asc())
        .limit(max_n)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


def attach_outage_to_payload(
    payload: dict[str, Any],
    *,
    work,
) -> dict[str, Any]:
    """
    If this work was delayed, inject safe outage context for the tutor.
    Does not overwrite existing outage_context.
    """
    if not payload:
        payload = {}
    if payload.get("outage_context"):
        return payload
    meta = work.metadata_ or {}
    if not (meta.get("recovery") or payload.get("recovery")):
        # Also detect long queue delay from created_at
        if not work.created_at:
            return payload
        wait = (datetime.now(timezone.utc) - work.created_at).total_seconds()
        settings = get_settings()
        min_s = float(getattr(settings, "outage_awareness_min_seconds", 30) or 30)
        if wait < min_s:
            return payload
        ctx = build_outage_context(wait_seconds=wait, preserved_message_count=1)
        if ctx:
            payload = {**payload, "outage_context": ctx, "recovery": True}
        return payload
    # recovery path
    wait = 0.0
    if work.created_at:
        wait = (datetime.now(timezone.utc) - work.created_at).total_seconds()
    ctx = build_outage_context(wait_seconds=wait, preserved_message_count=1)
    if ctx:
        payload = {**payload, "outage_context": ctx}
    return payload
