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
from wax.delivery.retry import DeliveryRetryService
from wax.memory.consolidation import MemoryConsolidationService
from wax.terminal.cleanup import cleanup_old_files
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
    # Optional async media fetch before tutor (still outside webhook)
    payload = work.input_payload or {}
    if payload.get("media_id") and not payload.get("local_media_path"):
        try:
            from wax.messaging.media import fetch_whatsapp_media, fetch_telegram_media
            channel = payload.get("channel")
            if channel == "whatsapp":
                fetched = await fetch_whatsapp_media(payload["media_id"], work.principal_id)
            elif channel == "telegram":
                fetched = await fetch_telegram_media(payload["media_id"], work.principal_id)
            else:
                fetched = {}
            if fetched.get("ok"):
                payload = {**payload, "local_media_path": fetched.get("path")}
                work.input_payload = payload
                await session.flush()
        except Exception:
            logger.exception("media_prepare_inline_failed")

    # Auto-transcribe voice notes so the tutor receives text equivalent to typing
    local_path = payload.get("local_media_path")
    content_type = (payload.get("content_type") or "").lower()
    if (
        local_path
        and not payload.get("transcript")
        and (
            content_type == "audio"
            or str(local_path).lower().endswith((".ogg", ".oga", ".mp3", ".m4a", ".wav", ".webm"))
        )
    ):
        try:
            from wax.tools.transcription import transcribe_local_audio

            tr = await transcribe_local_audio(str(local_path))
            if tr.get("ok") and tr.get("transcript"):
                payload = {
                    **payload,
                    "transcript": tr["transcript"],
                    "text": tr["transcript"],
                }
                work.input_payload = payload
                await session.flush()
                logger.info(
                    "audio_auto_transcribed",
                    work_id=str(work.id),
                    chars=len(tr["transcript"]),
                )
            else:
                logger.info(
                    "audio_auto_transcribe_skipped",
                    work_id=str(work.id),
                    error=tr.get("error"),
                )
        except Exception:
            logger.exception("audio_auto_transcribe_failed")

    tutor = TutorService(session)
    try:
        await renew_lease(session, work, work.claimed_by or "worker")
        result = await tutor.handle_message(work)
        work.status = "completed"
        work.completed_at = datetime.now(timezone.utc)
        work.result_payload = {
            "reply_preview": (result.get("reply") or "")[:400],
            "memories_used": result.get("memories_used", 0),
            "tools": result.get("tools") or [],
            "interactive": result.get("interactive"),
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





async def process_memory_work(session, work: Work) -> None:
    """Post-turn memory — never blocks learner-facing reply."""
    from wax.memory.service import MemoryService
    from wax.memory.observations import ObservationService
    from wax.intelligence.session_continuity import SessionContinuityService
    from wax.memory.evidence import EvidenceService

    payload = work.input_payload or {}
    principal_id = work.principal_id
    conversation_id = payload.get("conversation_id") or work.conversation_id
    recent = list(payload.get("recent") or [])
    user_text = payload.get("user_text") or ""
    reply_text = payload.get("reply_text") or ""
    recent = recent + [
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": reply_text},
    ]
    try:
        if principal_id:
            await MemoryService(session).extract_and_store(
                principal_id=principal_id,
                conversation_id=str(conversation_id) if conversation_id else None,
                recent_messages=recent,
                source_work_id=payload.get("source_work_id"),
            )
            try:
                obs = await ObservationService(session).record(
                    principal_id=principal_id,
                    kind="tutor_turn",
                    content=(user_text[:200] + " → " + reply_text[:200]),
                    weight=0.4,
                    conversation_id=conversation_id,
                    work_id=work.id,
                )
                # Selective evidence from explicit learner statements (not every sentence)
                ev = EvidenceService(session)
                lower = (user_text or "").lower()
                if any(x in lower for x in ("i don't understand", "i dont understand", "i'm confused", "i am confused")):
                    await ev.record(
                        principal_id=principal_id,
                        evidence_type="explicit",
                        description=f"Learner reported difficulty: {user_text[:240]}",
                        claim_key="signal:reported_difficulty",
                        payload={
                            "supports": True,
                            "text": user_text[:500],
                            "scope": "this_turn",
                            "not_yet_durable_preference": True,
                        },
                        assistance_level="unknown",
                        weight=0.45,  # single turn — do not over-weight
                        directness=0.9,
                        source="explicit",
                        observation_id=getattr(obs, "id", None),
                        work_id=work.id,
                    )
                if any(x in lower for x in ("i prefer", "works better when", "show me", "real-life", "example")):
                    await ev.record(
                        principal_id=principal_id,
                        evidence_type="explicit",
                        description=f"Possible teaching preference: {user_text[:240]}",
                        claim_key="preference:teaching_style",
                        payload={
                            "supports": True,
                            "text": user_text[:500],
                            "scope": "this_turn",
                            "candidate_preference": True,
                        },
                        weight=0.4,  # needs repetition before durable belief
                        source="explicit",
                        observation_id=getattr(obs, "id", None),
                        work_id=work.id,
                    )
            except Exception:
                logger.exception("evidence_extraction_failed")
            try:
                await SessionContinuityService(session).maybe_digest(
                    principal_id=principal_id,
                    conversation_id=conversation_id,
                    every_n=20,
                )
            except Exception:
                pass
        work.status = "completed"
        work.completed_at = datetime.now(timezone.utc)
        await session.flush()
    except Exception as e:
        logger.exception("memory_work_failed", work_id=str(work.id))
        work.error = str(e)
        work.error_class = "memory_failure"
        work.status = "failed"
        work.completed_at = datetime.now(timezone.utc)
        await session.flush()


async def process_media_prepare(session, work: Work) -> None:
    """Async media download into workspace — never in webhook."""
    from wax.messaging.media import fetch_whatsapp_media, fetch_telegram_media

    payload = work.input_payload or {}
    channel = payload.get("channel")
    media_id = payload.get("media_id")
    principal_id = work.principal_id
    try:
        if channel == "whatsapp" and media_id and principal_id:
            result = await fetch_whatsapp_media(media_id, principal_id)
        elif channel == "telegram" and media_id and principal_id:
            result = await fetch_telegram_media(media_id, principal_id)
        else:
            result = {"ok": False, "error": "no_media"}
        work.status = "completed"
        work.completed_at = datetime.now(timezone.utc)
        work.result_payload = result
        await session.flush()
    except Exception as e:
        work.error = str(e)
        work.error_class = "media_failure"
        work.status = "failed"
        work.completed_at = datetime.now(timezone.utc)
        await session.flush()


async def process_scheduled_action(session, work: Work) -> None:
    """Scheduled follow-ups go through intelligence, then durable delivery."""
    from uuid import UUID
    from wax.scheduler.service import SchedulerService
    from wax.domain.identity import primary_channel_target
    from wax.db.models import ScheduledAction, Delivery

    tutor = TutorService(session)
    try:
        result = await tutor.handle_scheduled_action(work)
        reply = (result.get("reply") or "").strip()
        work.status = "completed"
        work.completed_at = datetime.now(timezone.utc)
        work.result_payload = {"reply_preview": reply[:400]}

        action_id = (work.input_payload or {}).get("scheduled_action_id")
        if action_id:
            try:
                action = await session.get(ScheduledAction, UUID(str(action_id)))
            except Exception:
                action = await session.get(ScheduledAction, action_id)
            if action:
                await SchedulerService(session).complete(
                    action, result={"reply": reply}
                )

        if work.principal_id and reply:
            target = await primary_channel_target(session, work.principal_id)
            if target:
                channel, external_id = target
                from wax.delivery.presentation import present_for_channel

                rendered = present_for_channel(reply, channel)
                delivery = Delivery(
                    id=uuid4(),
                    work_id=work.id,
                    principal_id=work.principal_id,
                    channel=channel,
                    target_external_id=external_id,
                    content=rendered,
                    status="pending",
                    idempotency_key=f"sched-delivery:{work.id}",
                    metadata_={"canonical_preview": reply[:500]},
                )
                session.add(delivery)
                await session.flush()
                await _attempt_deliveries(session, work.id)

        await session.flush()
        logger.info("scheduled_action_completed", work_id=str(work.id))
    except Exception as e:
        logger.exception("scheduled_action_failed", work_id=str(work.id))
        work.error = str(e)
        work.error_class = "execution_failure"
        work.status = "failed"
        work.completed_at = datetime.now(timezone.utc)
        await session.flush()

async def _attempt_deliveries(session, work_id) -> None:
    from wax.delivery.senders import deliver as channel_deliver
    from wax.db.models import Artifact
    from wax.artifacts.storage import read_bytes

    stmt = select(Delivery).where(
        Delivery.work_id == work_id, Delivery.status == "pending"
    )
    result = await session.execute(stmt)
    for delivery in result.scalars().all():
        try:
            wrow = await session.get(Work, work_id)
            interactive = (wrow.result_payload or {}).get("interactive") if wrow else None
            tools = (wrow.result_payload or {}).get("tools") or [] if wrow else []
            inbound_mid = None
            meta = delivery.metadata_ or {}
            if isinstance(meta, dict):
                inbound_mid = meta.get("inbound_external_id") or meta.get("source_message_id")
            outcome = await channel_deliver(
                delivery.channel,
                delivery.target_external_id,
                delivery.content,
                interactive=interactive,
                inbound_message_id=inbound_mid,
                show_typing=True,
            )
            if outcome.get("status") in ("ok", "skipped"):
                delivery.status = "delivered"
                delivery.delivered_at = datetime.now(timezone.utc)
                # Prefer real provider message id when present
                real_id = outcome.get("external_message_id") or outcome.get("message_id")
                if not real_id:
                    results = outcome.get("results") or []
                    for r in results:
                        if isinstance(r, dict) and r.get("message_id"):
                            real_id = str(r["message_id"])
                            break
                delivery.external_message_id = (
                    str(real_id) if real_id else f"out-{uuid4().hex[:12]}"
                )
                delivery.metadata_ = {**(delivery.metadata_ or {}), "outcome": outcome}
                logger.info(
                    "delivery_succeeded",
                    delivery_id=str(delivery.id),
                    channel=delivery.channel,
                    external_message_id=delivery.external_message_id,
                )
            else:
                delivery.status = "failed"
                delivery.error = str(outcome)
                delivery.attempt += 1
                logger.warning(
                    "delivery_failed",
                    delivery_id=str(delivery.id),
                    channel=delivery.channel,
                    error=str(outcome)[:200],
                )

            # Artifact file delivery (Telegram document) when tools created one
            for tool in tools:
                if not isinstance(tool, dict):
                    continue
                if tool.get("name") != "create_artifact":
                    continue
                out = tool.get("result") or tool.get("outcome") or {}
                if not out.get("ok") or not out.get("artifact_id"):
                    continue
                if out.get("delivered"):
                    continue
                try:
                    from uuid import UUID
                    art = await session.get(Artifact, UUID(str(out["artifact_id"])))
                    if not art:
                        continue
                    uri = (art.structured or {}).get("storage_uri")
                    if not uri:
                        continue
                    data = read_bytes(uri)
                    if delivery.channel == "telegram":
                        from wax.messaging.telegram.client import send_document
                        ext = "pdf" if "pdf" in (art.content_type or "") else "txt"
                        doc_out = await send_document(
                            delivery.target_external_id,
                            filename=f"{(art.title or 'notes')[:40]}.{ext}",
                            data=data,
                            caption=art.title,
                            content_type=art.content_type or "application/octet-stream",
                        )
                        if doc_out.get("status") == "ok":
                            structured = dict(art.structured or {})
                            structured["delivered"] = True
                            structured["telegram_file_id"] = doc_out.get("file_id")
                            art.structured = structured
                            logger.info(
                                "artifact_delivery_succeeded",
                                artifact_id=str(art.id),
                                channel="telegram",
                            )
                        else:
                            logger.warning(
                                "artifact_delivery_failed",
                                artifact_id=str(art.id),
                                outcome=str(doc_out)[:200],
                            )
                except Exception:
                    logger.exception("artifact_delivery_error")
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
    # Lease metadata for recovery (stale claim detection)
    meta = dict(work.metadata_ or {})
    lease_s = int(getattr(settings, "work_stale_seconds", 300) or 300)
    from datetime import timedelta as _td
    meta["lease_until"] = (now + _td(seconds=lease_s)).isoformat()
    meta["lease_worker"] = worker_id
    work.metadata_ = meta
    work.attempt += 1
    await session.flush()
    work_id_var.set(str(work.id))
    logger.info("work_claimed", work_id=str(work.id), kind=work.kind, attempt=work.attempt)
    return work



async def renew_lease(session, work: Work, worker_id: str) -> None:
    """Heartbeat while processing — extends lease_until."""
    now = datetime.now(timezone.utc)
    if work.claimed_by != worker_id:
        return
    meta = dict(work.metadata_ or {})
    lease_s = int(getattr(settings, "work_stale_seconds", 300) or 300)
    meta["lease_until"] = (now + timedelta(seconds=lease_s)).isoformat()
    meta["lease_heartbeat_at"] = now.isoformat()
    work.metadata_ = meta
    work.claimed_at = now
    await session.flush()


async def reclaim_stale_works(session, limit: int = 20) -> int:
    """Re-queue works whose lease expired while status=running."""
    now = datetime.now(timezone.utc)
    stmt = (
        select(Work)
        .where(Work.status == "running")
        .order_by(Work.claimed_at.asc().nullsfirst())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    result = await session.execute(stmt)
    n = 0
    for work in result.scalars().all():
        meta = dict(work.metadata_ or {})
        lease_s = meta.get("lease_until")
        stale = False
        if lease_s:
            try:
                until = datetime.fromisoformat(str(lease_s).replace("Z", "+00:00"))
                if until.tzinfo is None:
                    until = until.replace(tzinfo=timezone.utc)
                stale = until < now
            except Exception:
                stale = True
        elif work.claimed_at:
            age = (now - work.claimed_at).total_seconds()
            stale = age > float(getattr(settings, "work_stale_seconds", 300) or 300)
        if not stale:
            continue
        work.status = "retrying"
        work.claimed_by = None
        work.error = work.error or "lease_expired"
        work.error_class = "lease_expired"
        work.next_retry_at = now
        work.attempt = int(work.attempt or 0) + 1
        n += 1
        logger.warning("work_lease_reclaimed", work_id=str(work.id), attempt=work.attempt)
    if n:
        await session.flush()
    return n


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
                elif work.kind == "memory_process":
                    await process_memory_work(session, work)
                elif work.kind == "media_prepare":
                    await process_media_prepare(session, work)
                elif work.kind == "scheduled_action":
                    await process_scheduled_action(session, work)
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
    cycle = 0
    while RUNNING:
        # Isolate subsystems so one failure does not skip the rest of the cycle.
        try:
            async with session_scope() as session:
                try:
                    await recover_orphans(session)
                except Exception:
                    logger.exception("recovery_orphans_error")
                try:
                    from wax.scheduler.service import SchedulerService
                    sched = SchedulerService(session)
                    due = await sched.due_actions(limit=10)
                    for action in due:
                        await sched.mark_executing(action)
                        await sched.create_work_for_action(action)
                        logger.info(
                            "scheduled_action_claimed",
                            action_id=str(action.id),
                            work_id=str(action.work_id) if action.work_id else None,
                        )
                except Exception:
                    logger.exception("scheduler_failure")
                try:
                    n = await reclaim_stale_works(session, limit=20)
                    if n:
                        logger.info("stale_works_reclaimed", count=n)
                    from wax.interaction.service import InteractionService
                    expired_ix = await InteractionService(session).expire_due(limit=30)
                    for ix in expired_ix:
                        try:
                            await InteractionService(session).enqueue_continuation(
                                ix, reason="timeout"
                            )
                        except Exception:
                            logger.exception(
                                "interaction_timeout_enqueue_failed",
                                interaction_id=str(ix.id),
                            )
                except Exception:
                    logger.exception("interaction_expiry_error")
                try:
                    from wax.db.models import Activity
                    now = datetime.now(timezone.utc)
                    exp = await session.execute(
                        select(Activity).where(
                            Activity.status == "active",
                            Activity.ends_at.is_not(None),
                            Activity.ends_at < now,
                        ).limit(20)
                    )
                    for act in exp.scalars().all():
                        act.status = "expired"
                    await session.flush()
                except Exception:
                    logger.exception("activity_expiry_error")
                try:
                    retried = await DeliveryRetryService(session).process_batch(limit=10)
                    if retried:
                        logger.info("deliveries_retried", count=retried)
                except Exception:
                    logger.exception("delivery_retry_error")
                cycle += 1
                if cycle % 30 == 0:
                    try:
                        cleanup_old_files()
                    except Exception:
                        logger.exception("workspace_cleanup_error")
                if cycle % 10 == 0:
                    try:
                        from wax.db.models import Principal
                        result = await session.execute(select(Principal.id).limit(5))
                        for (pid,) in result.all():
                            try:
                                await MemoryConsolidationService(session).consolidate_principal(pid)
                            except Exception:
                                logger.exception(
                                    "memory_consolidation_failed",
                                    principal_id=str(pid),
                                )
                    except Exception:
                        logger.exception("memory_consolidation_batch_error")
        except Exception:
            logger.exception("recovery_error")
        await asyncio.sleep(max(5.0, settings.scheduler_poll_interval_seconds))


def _log_provider_config() -> None:
    """Safe startup diagnostics — never log API keys."""
    key = (settings.primary_api_key or "").strip()
    placeholder = key in ("", "REPLACE_WITH_YOUR_LLM_API_KEY", "change-me")
    base = (settings.primary_base_url or "").strip() or "(default)"
    logger.info(
        "provider_config",
        provider_selected=settings.primary_provider,
        provider_api_key_configured=bool(key) and not placeholder,
        provider_api_key_looks_placeholder=placeholder,
        provider_base_url=base,
        provider_model=settings.primary_model,
        fallback_provider=settings.fallback_provider,
        fallback_api_key_configured=bool((settings.fallback_api_key or "").strip()),
    )


async def main() -> None:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    _log_provider_config()
    tasks = [asyncio.create_task(worker_loop(f"worker-{i}")) for i in range(2)]
    tasks.append(asyncio.create_task(recovery_loop()))
    await asyncio.gather(*tasks)


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
