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
        # Idempotent: if message already has work_id (race), skip
        if msg.work_id:
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


# ─── Delayed-work coalescing (related turns → one reply) ───────────────────

def _token_set(text: str) -> set[str]:
    return {w for w in "".join(c.lower() if c.isalnum() else " " for c in (text or "")).split() if len(w) > 1}


def relatedness_score(
    text_a: str,
    text_b: str,
    *,
    seconds_apart: float,
) -> float:
    """
    How related two inbound texts are for coalescing into one tutor turn.

    Structure + overlap + temporal proximity — not a subject keyword router.
    """
    a, b = _token_set(text_a), _token_set(text_b)
    if not a and not b:
        return 0.0
    if not a or not b:
        # One empty — still coalesce if very close in time (fragment burst)
        return 0.55 if seconds_apart <= 45 else 0.15
    inter = len(a & b)
    union = len(a | b) or 1
    jaccard = inter / union
    # Short follow-ups (low token count, high overlap with prior or shared pronouns density)
    short_b = len(b) <= 4
    temporal = 1.0 / (1.0 + (seconds_apart / 60.0))
    score = 0.45 * jaccard + 0.35 * temporal
    if short_b and seconds_apart <= 90:
        score += 0.2
    if inter >= 2:
        score += 0.1
    return min(1.0, score)


async def find_coalescable_works(
    session,
    *,
    anchor_work,
    window_seconds: float | None = None,
    min_relatedness: float = 0.42,
    max_siblings: int = 6,
) -> list:
    """
    Find other message_response works for the same principal+conversation that
    should fold into this turn (queued / retrying, not yet delivered).
    """
    from sqlalchemy import select
    from wax.db.models import Work

    settings = get_settings()
    window = float(
        window_seconds
        if window_seconds is not None
        else getattr(settings, "recovery_batch_window_seconds", 90) or 90
    )
    # Under recovery pressure, allow a wider window
    meta = dict(anchor_work.metadata_ or {})
    if meta.get("recovery") or (anchor_work.input_payload or {}).get("recovery"):
        window = max(window, 180.0)

    if not anchor_work.principal_id or not anchor_work.conversation_id:
        return []

    now = datetime.now(timezone.utc)
    anchor_t = anchor_work.created_at or now
    stmt = (
        select(Work)
        .where(
            Work.principal_id == anchor_work.principal_id,
            Work.conversation_id == anchor_work.conversation_id,
            Work.kind == "message_response",
            Work.id != anchor_work.id,
            Work.status.in_(("queued", "retrying")),
        )
        .order_by(Work.created_at.asc())
        .limit(20)
        .with_for_update(skip_locked=True)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    anchor_text = str((anchor_work.input_payload or {}).get("text") or "")
    selected = []
    for w in rows:
        wt = w.created_at or now
        gap = abs((wt - anchor_t).total_seconds())
        if gap > window and not (meta.get("recovery") or (w.metadata_ or {}).get("recovery")):
            # Still allow if both marked recovery/outage even if slightly outside window
            if gap > window * 2:
                continue
        other_text = str((w.input_payload or {}).get("text") or "")
        # Always coalesce pure recovery backlog in same conversation within 2x window
        both_recovery = bool(
            (meta.get("recovery") or (anchor_work.input_payload or {}).get("recovery"))
            and ((w.metadata_ or {}).get("recovery") or (w.input_payload or {}).get("recovery"))
        )
        score = relatedness_score(anchor_text, other_text, seconds_apart=gap)
        if both_recovery and gap <= window * 2:
            score = max(score, 0.5)
        if score >= min_relatedness:
            selected.append((score, w))
    selected.sort(key=lambda x: x[0], reverse=True)
    return [w for _, w in selected[:max_siblings]]


async def coalesce_works_into(
    session,
    *,
    primary,
    siblings: list,
) -> dict:
    """
    Fold sibling works into primary payload; mark siblings coalesced (no separate reply).

    Original Message rows and sibling Work rows remain for audit — siblings complete
    with status completed and result coalesced_into.
    """
    if not siblings:
        return {"coalesced": 0, "texts": []}

    payload = dict(primary.input_payload or {})
    texts: list[dict] = []
    # Primary first by time
    chain = sorted(
        [primary, *siblings],
        key=lambda w: w.created_at or datetime.now(timezone.utc),
    )
    for w in chain:
        pl = w.input_payload or {}
        t = str(pl.get("text") or "").strip()
        if t:
            texts.append(
                {
                    "work_id": str(w.id),
                    "text": t[:2000],
                    "created_at": w.created_at.isoformat() if w.created_at else None,
                }
            )
    if len(texts) <= 1:
        return {"coalesced": 0, "texts": texts}

    # Combined user text: ordered sequence for the tutor (one turn)
    combined = "\n".join(f"- {x['text']}" for x in texts)
    payload["text"] = texts[-1]["text"]  # latest remains primary surface text
    payload["coalesced_messages"] = texts
    payload["coalesced_combined"] = combined
    payload["recovery_batch"] = texts  # tutor already understands recovery_batch shape
    payload["recovery"] = True
    meta = dict(primary.metadata_ or {})
    meta["coalesced_work_ids"] = [str(s.id) for s in siblings]
    primary.input_payload = payload
    primary.metadata_ = meta

    now = datetime.now(timezone.utc)
    for s in siblings:
        s.status = "completed"
        s.completed_at = now
        s.result_payload = {
            "coalesced_into": str(primary.id),
            "reason": "related_delayed_turn",
        }
        s.error = None
        sm = dict(s.metadata_ or {})
        sm["coalesced_into"] = str(primary.id)
        s.metadata_ = sm

    await session.flush()
    return {"coalesced": len(siblings), "texts": texts}
