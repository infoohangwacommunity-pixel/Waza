"""
WAX Prep background worker.

Claims durable Work → runs Tutor intelligence → delivery.
Survives crashes. Every student message goes through AI.
Only pure infrastructure error paths skip the model.
Durable memory is AI-driven via state directives — no post-turn memory job.
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
from wax.world.cleanup import cleanup_old_files
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
    """Full AI tutor path — every student message passes through intelligence."""
    from wax.observability.turn_telemetry import begin_turn, end_turn, get_turn

    payload0 = work.input_payload or {}
    begin_turn(
        work_id=str(work.id),
        principal_id=str(work.principal_id or ""),
        channel=str(payload0.get("channel") or ""),
    )
    # Safe outage / recovery context for the tutor (no internals)
    from wax.work.recovery import (
        attach_outage_to_payload,
        find_recovery_batch_candidates,
        build_outage_context,
        find_coalescable_works,
        coalesce_works_into,
    )

    payload = work.input_payload or {}
    payload = attach_outage_to_payload(payload, work=work)

    # Operational coalesce: related queued/retrying works for same conversation → one turn
    try:
        if work.principal_id and work.conversation_id:
            siblings = await find_coalescable_works(session, anchor_work=work)
            if siblings:
                result = await coalesce_works_into(
                    session, primary=work, siblings=siblings
                )
                payload = work.input_payload or payload
                logger.info(
                    "works_coalesced",
                    work_id=str(work.id),
                    coalesced=result.get("coalesced"),
                    texts=len(result.get("texts") or []),
                )
    except Exception:
        logger.exception("work_coalesce_failed")

    # Recovery batching: if this is a recovered/delayed work, include nearby sibling
    # inbound messages as ordered context. Original Message rows are never deleted.
    if (payload.get("recovery") or (work.metadata_ or {}).get("recovery") or payload.get("coalesced_messages")) and work.principal_id and work.conversation_id:
        try:
            anchor = work.created_at or datetime.now(timezone.utc)
            siblings = await find_recovery_batch_candidates(
                session,
                principal_id=work.principal_id,
                conversation_id=work.conversation_id,
                anchor_time=anchor,
            )
            if len(siblings) > 1:
                ordered = [
                    {"message_id": str(m.id), "text": m.content, "created_at": m.created_at.isoformat() if m.created_at else None}
                    for m in siblings
                ]
                payload = {
                    **payload,
                    "recovery_batch": ordered,
                    "outage_context": build_outage_context(
                        wait_seconds=(datetime.now(timezone.utc) - (siblings[0].created_at or anchor)).total_seconds(),
                        preserved_message_count=len(siblings),
                    ) or payload.get("outage_context"),
                }
                # Prefer the combined text in original order for the tutor if current text is partial
                if not (payload.get("text") or "").strip() and ordered:
                    payload["text"] = ordered[-1]["text"]
                logger.info(
                    "recovery_batch_attached",
                    work_id=str(work.id),
                    batch_size=len(siblings),
                )
        except Exception:
            logger.exception("recovery_batch_failed")

    if payload is not work.input_payload:
        work.input_payload = payload
        await session.flush()

    # Rate / workload note (post-accept): never drops the message; may defer priority
    if work.principal_id:
        try:
            from wax.protection.rate import decide_durable, RateDecision

            decision = await decide_durable(session, work.principal_id)
            if decision in (RateDecision.THROTTLE, RateDecision.PROTECT, RateDecision.DEFER):
                meta = dict(work.metadata_ or {})
                meta["rate_decision"] = decision.value
                work.metadata_ = meta
                # Soft backpressure: lower priority, keep queued for later claim
                if decision == RateDecision.PROTECT and work.status == "running":
                    work.priority = min(int(work.priority or 100) + 50, 200)
                logger.info(
                    "rate_decision",
                    work_id=str(work.id),
                    principal_id=str(work.principal_id),
                    decision=decision.value,
                )
        except Exception:
            logger.exception("rate_decision_failed")

    # Media: download + place in World only. AI decides whether to inspect.
    payload = work.input_payload or {}
    if payload.get("media_id") and not payload.get("local_media_path") and work.principal_id:
        try:
            from wax.messaging.media import fetch_whatsapp_media, fetch_telegram_media

            channel = payload.get("channel")
            if channel == "whatsapp":
                fetched = await fetch_whatsapp_media(payload["media_id"], work.principal_id)
            elif channel == "telegram":
                fetched = await fetch_telegram_media(payload["media_id"], work.principal_id)
            else:
                fetched = {}
            if fetched.get("ok") and fetched.get("path"):
                local = str(fetched["path"])
                payload = {
                    **payload,
                    "local_media_path": local,
                    "principal_media_path": local,
                    "media_mime": fetched.get("mime"),
                    "media_size": fetched.get("size"),
                }
                work.input_payload = payload
                await session.flush()
                logger.info(
                    "media_placed_in_world",
                    work_id=str(work.id),
                    path=local,
                    mime=fetched.get("mime"),
                    size=fetched.get("size"),
                )
        except Exception:
            logger.exception("media_world_stage_failed")

    tutor = TutorService(session)
    try:
        await renew_lease(session, work, work.claimed_by or "worker")
        # Progressive presence: typing indicator while intelligence runs
        try:
            from wax.delivery.senders import send_typing

            pl = work.input_payload or {}
            tgt = pl.get("target_external_id")
            ch = pl.get("channel") or ""
            # WhatsApp typing/read needs the provider wamid — not our internal Message UUID
            inbound = pl.get("provider_message_id") or pl.get("wamid") or pl.get("external_id")
            if not inbound and ch != "whatsapp":
                inbound = pl.get("message_id")
            if tgt and ch:
                await send_typing(
                    ch,
                    str(tgt),
                    inbound_message_id=str(inbound) if inbound else None,
                )
        except Exception:
            logger.exception("typing_indicator_failed")
        result = await tutor.handle_message(work)
        # Durable World transcript archive (idempotent, restart-safe): the DB
        # rows are already flushed; mirror them into the student's World.
        try:
            from wax.world.transcript import archive_turn

            await archive_turn(session, work)
        except Exception:
            logger.exception("world_transcript_archive_turn_failed", work_id=str(work.id))
        work.status = "completed"
        work.completed_at = datetime.now(timezone.utc)
        reply = (result.get("reply") or "").strip()
        interactive = result.get("interactive")
        work.result_payload = {
            "reply_preview": reply[:400],
            "interactive": interactive,
        }
        await session.flush()

        # Durable outbound delivery: one pending Delivery row per successful tutor turn.
        # This is the single outbound delivery path for normal tutor responses.
        # _attempt_deliveries() below processes pending Delivery rows — it does not
        # call WhatsApp directly. The competing direct path in TutorService._deliver()
        # is removed so that only this durable path reaches Meta.
        try:
            from wax.delivery.presentation import present_for_channel

            pl = work.input_payload or {}
            channel = pl.get("channel") or "whatsapp"
            target = pl.get("target_external_id")
            inbound_mid = (
                pl.get("provider_message_id")
                or pl.get("wamid")
                or pl.get("external_id")
            )
            if target and work.principal_id and reply:
                rendered = present_for_channel(reply, channel)
                idem = f"tutor-reply:{work.id}"
                existing = await session.scalar(
                    select(Delivery.id).where(Delivery.idempotency_key == idem)
                )
                if not existing:
                    delivery = Delivery(
                        id=uuid4(),
                        work_id=work.id,
                        principal_id=work.principal_id,
                        channel=channel,
                        target_external_id=str(target),
                        content=rendered,
                        status="pending",
                        idempotency_key=idem,
                        metadata_={
                            "inbound_external_id": str(inbound_mid) if inbound_mid else None,
                            "source_message_id": str(inbound_mid) if inbound_mid else None,
                            "canonical_preview": reply[:500],
                        },
                    )
                    session.add(delivery)
                    await session.flush()
                    logger.info(
                        "delivery_queued",
                        delivery_id=str(delivery.id),
                        work_id=str(work.id),
                        channel=channel,
                        target_external_id=str(target),
                        content_chars=len(rendered),
                        inbound_message_id=str(inbound_mid) if inbound_mid else None,
                    )
                else:
                    logger.info(
                        "delivery_queued_skipped_duplicate",
                        work_id=str(work.id),
                        idempotency_key=idem,
                    )
        except Exception:
            logger.exception("delivery_queued_failed", work_id=str(work.id))

        logger.info("work_completed", work_id=str(work.id))
        tel = get_turn()
        if tel:
            acts = (result or {}).get("actions") or []
            tel.directives_run = len(acts) if isinstance(acts, list) else 0
            end_turn()

        await _attempt_deliveries(session, work.id)
    except Exception as e:
        try:
            end_turn()
        except Exception:
            pass
        logger.exception("tutor_turn_failed", work_id=str(work.id))
        from wax.security.safe_errors import classify_error, student_facing_message

        work.error = str(e)[:1000]  # internal only
        work.error_class = classify_error(e)
        # Provider rate limit → wait at least retry_after before reclaiming work
        rate_delay = None
        try:
            from wax.intelligence.providers import ProviderError, ProviderErrorClass

            if isinstance(e, ProviderError) and e.error_class == ProviderErrorClass.RATE_LIMITED:
                rate_delay = float(getattr(e, "retry_after_seconds", None) or 20.0)
                work.error_class = "provider_rate_limited"
        except Exception:
            if "Rate limited" in str(e) or "429" in str(e):
                rate_delay = 20.0
                work.error_class = "provider_rate_limited"
        meta = dict(work.metadata_ or {})
        rate_limit_retries = int(meta.get("rate_limit_retries") or 0)

        # Rate limits must NOT burn permanent attempts — otherwise the user gets
        # "I'll continue when ready" and the work is never truly completed.
        if rate_delay is not None:
            rate_limit_retries += 1
            meta["rate_limit_retries"] = rate_limit_retries
            meta["last_rate_limit_at"] = datetime.now(timezone.utc).isoformat()
            work.metadata_ = meta
            # Undo this claim's attempt burn for pure provider pressure
            if work.attempt and work.attempt > 0:
                work.attempt = max(0, int(work.attempt) - 1)
            max_rl = int(getattr(settings, "provider_rate_limit_max_retries", 48) or 48)
            if rate_limit_retries <= max_rl:
                work.status = "retrying"
                delay = min(900, max(15.0, float(rate_delay)))
                # Back off harder as pressure continues
                delay = min(900, delay * (1.0 + 0.15 * min(rate_limit_retries, 10)))
                work.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
                work.error_class = "provider_rate_limited"
                logger.info(
                    "work_scheduled_retry_rate_limit",
                    work_id=str(work.id),
                    delay_seconds=round(delay, 1),
                    rate_limit_retries=rate_limit_retries,
                )
                # One interim student ack (idempotent) — promise we will finish
                try:

                    target = (work.input_payload or {}).get("target_external_id")
                    channel = (work.input_payload or {}).get("channel") or "whatsapp"
                    if target and work.principal_id:
                        idem = f"rate-ack:{work.id}"
                        existing = await session.scalar(
                            select(Delivery.id).where(Delivery.idempotency_key == idem)
                        )
                        if not existing:
                            session.add(
                                Delivery(
                                    id=uuid4(),
                                    work_id=work.id,
                                    principal_id=work.principal_id,
                                    channel=channel,
                                    target_external_id=str(target),
                                    content=student_facing_message(
                                        e, error_class="provider_rate_limited"
                                    ),
                                    status="pending",
                                    idempotency_key=idem,
                                )
                            )
                except Exception:
                    logger.exception("rate_ack_delivery_failed")
            else:
                work.status = "failed"
                work.completed_at = datetime.now(timezone.utc)
                logger.warning(
                    "work_rate_limit_exhausted",
                    work_id=str(work.id),
                    rate_limit_retries=rate_limit_retries,
                )
        elif work.attempt < work.max_attempts:
            work.status = "retrying"
            delay = min(300, 2 ** int(work.attempt or 0) * 5)
            work.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
            logger.info(
                "work_scheduled_retry",
                work_id=str(work.id),
                delay_seconds=delay,
                error_class=work.error_class,
            )
        else:
            work.status = "failed"
            work.completed_at = datetime.now(timezone.utc)
            # Permanent failure: queue a safe student-facing apology once
            try:

                safe = student_facing_message(e, error_class=work.error_class)
                target = (work.input_payload or {}).get("target_external_id")
                channel = (work.input_payload or {}).get("channel") or "whatsapp"
                if target and work.principal_id:
                    idem = f"safe-err:{work.id}"
                    existing = await session.scalar(
                        select(Delivery.id).where(Delivery.idempotency_key == idem)
                    )
                    if not existing:
                        session.add(
                            Delivery(
                                id=uuid4(),
                                work_id=work.id,
                                principal_id=work.principal_id,
                                channel=channel,
                                target_external_id=str(target),
                                content=safe,
                                status="pending",
                                idempotency_key=idem,
                            )
                        )
            except Exception:
                logger.exception("safe_error_delivery_failed")
        await session.flush()
        if work.status == "retrying":
            try:
                await _attempt_deliveries(session, work.id)
            except Exception:
                logger.exception("retry_ack_delivery_flush_failed")






async def process_surface_ai(session, work: Work) -> None:
    """Surface channel → same TutorService intelligence → apply result to surface."""
    from wax.surfaces.service import SurfaceService
    from wax.intelligence.tutor import TutorService

    payload = work.input_payload or {}
    request_id = payload.get("request_id")
    tutor = TutorService(session)
    svc = SurfaceService(session)
    try:
        await renew_lease(session, work, work.claimed_by or "worker")
        result = await tutor.handle_surface_request(work)
        work.status = "completed"
        work.completed_at = datetime.now(timezone.utc)
        work.result_payload = {
            "reply_preview": (result.get("reply") or "")[:400],
                    }
        if request_id:
            await svc.apply_ai_result(
                request_row_id=request_id,
                reply=result.get("reply"),
                            )
        await session.flush()
        logger.info("surface_ai_completed", work_id=str(work.id))
        # Optional channel delivery if surface work is also tied to a conversation
        try:
            await _attempt_deliveries(session, work.id)
        except Exception:
            logger.exception("surface_ai_delivery_skipped")
    except Exception as e:
        wid = str(work.id)
        logger.exception("surface_ai_failed", work_id=wid)
        if request_id:
            try:
                await session.rollback()
            except Exception:
                pass
            try:
                await svc.apply_ai_result(request_row_id=request_id, reply=None, error=str(e)[:300])
            except Exception:
                logger.exception("surface_ai_result_error", work_id=wid)
        if work.attempt < work.max_attempts:
            delay = min(300, 2 ** int(work.attempt or 0) * 5)
            await _mark_work_terminal(
                session,
                work,
                status="retrying",
                error=str(e),
                error_class="execution_failure",
                next_retry_at=datetime.now(timezone.utc) + timedelta(seconds=delay),
            )
        else:
            await _mark_work_terminal(
                session,
                work,
                status="failed",
                error=str(e),
                error_class="execution_failure",
            )



async def _mark_work_terminal(
    session,
    work: Work,
    *,
    status: str,
    error: str | None = None,
    error_class: str | None = None,
    next_retry_at=None,
) -> None:
    """Mark work completed/failed/retrying after a possible DB failure.

    Rolls back a poisoned transaction, then updates by primary key so we never
    depend on expired ORM state or flush a failed session.
    """
    work_id = getattr(work, "id", None)
    if work_id is None:
        return
    try:
        await session.rollback()
    except Exception:
        logger.exception("work_terminal_rollback_failed", work_id=str(work_id))
    values: dict = {"status": status}
    if error is not None:
        values["error"] = error[:1000]
    if error_class is not None:
        values["error_class"] = error_class
    if status in ("failed", "completed"):
        values["completed_at"] = datetime.now(timezone.utc)
    if next_retry_at is not None:
        values["next_retry_at"] = next_retry_at
    try:
        from sqlalchemy import update as sa_update

        await session.execute(sa_update(Work).where(Work.id == work_id).values(**values))
        await session.flush()
    except Exception:
        logger.exception("work_terminal_mark_failed", work_id=str(work_id), status=status)
        try:
            await session.rollback()
        except Exception:
            pass



async def process_scheduled_action(session, work: Work) -> None:
    """Scheduled follow-ups go through intelligence, then durable delivery."""
    from uuid import UUID
    from wax.scheduler.service import SchedulerService
    from wax.domain.identity import primary_channel_target
    from wax.db.models import ScheduledAction

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
        await _mark_work_terminal(
            session, work, status="failed", error=str(e), error_class="execution_failure"
        )

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
            inbound_mid = None
            meta = delivery.metadata_ or {}
            if isinstance(meta, dict):
                inbound_mid = meta.get("inbound_external_id") or meta.get("source_message_id")
            logger.info(
                "delivery_attempt",
                delivery_id=str(delivery.id),
                channel=delivery.channel,
                target_external_id=delivery.target_external_id,
                attempt=delivery.attempt + 1,
                inbound_message_id=str(inbound_mid) if inbound_mid else None,
            )
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
                delivery.error = None
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
                delivery.error = str(outcome)[:500]
                delivery.attempt += 1
                logger.warning(
                    "delivery_failed",
                    delivery_id=str(delivery.id),
                    channel=delivery.channel,
                    error=str(outcome)[:200],
                )

            # Artifact file delivery (Telegram document) when tools created one.
            # Populated from work.result_payload["actions"] when the agent emits artifact tools.
            tools = []
            if wrow and (wrow.result_payload or {}).get("actions"):
                tools = list((wrow.result_payload or {}).get("actions") or [])
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
    meta["lease_until"] = (now + timedelta(seconds=lease_s)).isoformat()
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



async def resuscitate_rate_limited_works(session, limit: int = 25) -> int:
    """
    Re-queue works that failed solely due to provider rate limits.

    Guarantees the "I'll continue once the system is ready" promise is kept
    when capacity returns — instead of leaving the work permanently failed.
    """
    now = datetime.now(timezone.utc)
    stmt = (
        select(Work)
        .where(
            Work.status == "failed",
            Work.kind == "message_response",
            Work.error_class.in_(("provider_rate_limited", "rate_protection", "transient_provider")),
        )
        .order_by(Work.updated_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    result = await session.execute(stmt)
    n = 0
    for work in result.scalars().all():
        meta = dict(work.metadata_ or {})
        rl = int(meta.get("rate_limit_retries") or 0)
        max_rl = int(getattr(settings, "provider_rate_limit_max_retries", 48) or 48)
        if rl >= max_rl:
            continue
        # Skip if a real reply delivery already exists for this work
        try:

            done = await session.scalar(
                select(Delivery.id).where(
                    Delivery.work_id == work.id,
                    Delivery.status == "delivered",
                    ~Delivery.idempotency_key.like("rate-ack:%"),
                    ~Delivery.idempotency_key.like("safe-err:%"),
                )
            )
            if done:
                continue
        except Exception:
            pass
        work.status = "retrying"
        work.completed_at = None
        work.claimed_by = None
        work.next_retry_at = now
        work.error = (work.error or "")[:500]
        meta["resuscitated_at"] = now.isoformat()
        work.metadata_ = meta
        n += 1
    if n:
        await session.flush()
    return n


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
    try:
        from wax.world.reconcile import reconcile_all_worlds

        report = reconcile_all_worlds()
        logger.info("world_startup_reconcile", worlds=report.get("worlds"), details=report.get("reports"))
    except Exception:
        logger.exception("world_startup_reconcile_failed")
    while RUNNING:
        try:
            async with session_scope() as session:
                work = await claim_next(session, worker_id)
                if not work:
                    await asyncio.sleep(settings.work_poll_interval_seconds)
                    continue
                if work.kind == "message_response":
                    await process_message_response(session, work)
                elif work.kind == "surface_ai":
                    await process_surface_ai(session, work)
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
                    from wax.work.recovery import recover_orphan_messages

                    n_orph = await recover_orphan_messages(session, limit=20)
                    if n_orph:
                        logger.warning("orphan_messages_recovered", count=n_orph)
                except Exception:
                    logger.exception("orphan_message_recovery_error")
                try:
                    from wax.scheduler.service import SchedulerService
                    sched = SchedulerService(session)
                    try:
                        await sched.recover_stuck_executing(older_than_seconds=300, limit=20)
                    except Exception:
                        logger.exception("scheduler_recover_stuck_failed")
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


                try:
                    from wax.surfaces.service import SurfaceService
                    ss = SurfaceService(session)
                    n_se = await ss.expire_due(limit=200)
                    n_si = await ss.mark_idle_dormant(limit=200)
                    n_sc = await ss.cleanup_expired(limit=50)
                    if n_se or n_si or n_sc:
                        logger.info("surface_lifecycle", expired=n_se, idle_dormant=n_si, cleaned=n_sc)
                except Exception:
                    logger.exception("surface_lifecycle_error")

                # Resuscitate works that died only to rate limits (promise to user was made)
                if cycle % 5 == 0:
                    try:
                        n = await resuscitate_rate_limited_works(session)
                        if n:
                            logger.info("resuscitated_rate_limited_works", count=n)
                    except Exception:
                        logger.exception("resuscitate_rate_limited_failed")

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
    # Student Worlds: fail closed if production persistence is required but path is /tmp
    try:
        from wax.config import validate_production_settings
        from wax.world.layout import workspace_root

        validate_production_settings(settings)
        root = workspace_root()
        logger.info(
            "world_store_ready",
            workspace_root=str(root),
            require_persistent=bool(settings.require_persistent_workspace),
            note="Worker owns authoritative Student World filesystem on this host",
        )
    except Exception:
        logger.exception("world_store_startup_failed")
        if settings.app_env == "production" and settings.require_persistent_workspace:
            raise
    tasks = [asyncio.create_task(worker_loop(f"worker-{i}")) for i in range(2)]
    tasks.append(asyncio.create_task(recovery_loop()))
    await asyncio.gather(*tasks)


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
