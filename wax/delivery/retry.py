"""
Durable outbound delivery retries.

AI success must not be lost if WhatsApp/Telegram fails once.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.config import get_settings
from wax.db.models import Delivery
from wax.delivery.senders import deliver
from wax.observability.logging import get_logger

from wax.delivery.backoff import retry_backoff_seconds

logger = get_logger(__name__)
settings = get_settings()



class DeliveryRetryService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def pending(self, limit: int = 20) -> list[Delivery]:
        now = datetime.now(timezone.utc)
        stmt = (
            select(Delivery)
            .where(
                Delivery.status.in_(["pending", "failed"]),
                Delivery.attempt < Delivery.max_attempts,
            )
            .where(
                (Delivery.next_retry_at.is_(None)) | (Delivery.next_retry_at <= now)
            )
            .order_by(Delivery.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def attempt_one(self, delivery: Delivery, interactive: dict | None = None) -> dict[str, Any]:
        delivery.attempt += 1
        delivery.status = "sending"
        await self.session.flush()
        meta = delivery.metadata_ or {}
        inbound_mid = None
        if isinstance(meta, dict):
            inbound_mid = meta.get("inbound_external_id") or meta.get("source_message_id")
        try:
            outcome = await deliver(
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
                delivery.external_message_id = (
                    outcome.get("external_message_id")
                    or delivery.external_message_id
                    or f"out-{delivery.attempt}"
                )
                delivery.metadata_ = {**(delivery.metadata_ or {}), "last_outcome": outcome}
                delivery.error = None
                logger.info(
                    "delivery_retry_succeeded",
                    delivery_id=str(delivery.id),
                    channel=delivery.channel,
                    attempt=delivery.attempt,
                )
            else:
                delivery.status = "failed"
                delivery.error = str(outcome)[:500]
                delay = retry_backoff_seconds(delivery.attempt)
                delivery.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
                logger.warning(
                    "delivery_retry_deferred",
                    delivery_id=str(delivery.id),
                    attempt=delivery.attempt,
                    next_retry_at=delivery.next_retry_at.isoformat(),
                )
            await self.session.flush()
            return outcome
        except Exception as e:
            delivery.status = "failed"
            delivery.error = str(e)[:500]
            delay = retry_backoff_seconds(delivery.attempt)
            delivery.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
            await self.session.flush()
            logger.exception("delivery_retry_failed", delivery_id=str(delivery.id))
            return {"status": "failed", "error": str(e)}

    async def process_batch(self, limit: int = 15) -> int:
        items = await self.pending(limit=limit)
        count = 0
        for d in items:
            await self.attempt_one(d)
            count += 1
        return count
