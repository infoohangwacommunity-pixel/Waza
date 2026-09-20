"""Server-authoritative interactions — durable choices with expire/consume.

Channel adapters only render. Validity is decided here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import Interaction, Work
from wax.observability.logging import get_logger

logger = get_logger(__name__)


def _token() -> str:
    # Telegram callback_data max 64 bytes; keep opaque and short
    return uuid4().hex[:24]


class InteractionService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        *,
        principal_id,
        channel: str,
        choices: list[dict[str, Any]],
        prompt: str | None = None,
        style: str = "buttons",
        work_id=None,
        activity_id=None,
        conversation_id=None,
        expires_in_seconds: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Interaction:
        now = datetime.now(timezone.utc)
        expires_at = None
        if expires_in_seconds is not None and expires_in_seconds > 0:
            expires_at = now + timedelta(seconds=int(expires_in_seconds))

        # Per-choice opaque callback tokens (platform sends these back)
        enriched = []
        for i, c in enumerate(choices[:10]):
            cid = str(c.get("id") or f"opt_{i}")[:128]
            title = str(c.get("title") or c.get("label") or cid)[:64]
            cb = f"ix:{_token()}"[:64]
            enriched.append(
                {
                    "id": cid,
                    "title": title,
                    "description": c.get("description"),
                    "callback_data": cb,
                }
            )

        # Root token is first choice's family id for lookup by any choice
        root = _token()
        ix = Interaction(
            id=uuid4(),
            principal_id=principal_id,
            activity_id=activity_id,
            work_id=work_id,
            conversation_id=conversation_id,
            channel=channel,
            callback_token=root[:64],
            prompt=prompt,
            style=style,
            choices=enriched,
            status="pending",
            expires_at=expires_at,
            metadata_=metadata or {},
        )
        self.session.add(ix)
        await self.session.flush()
        logger.info(
            "interactive_presented",
            interaction_id=str(ix.id),
            principal_id=str(principal_id),
            channel=channel,
            expires_at=expires_at.isoformat() if expires_at else None,
            n_choices=len(enriched),
        )
        return ix

    async def find_by_callback(
        self, callback_data: str, *, principal_id=None
    ) -> Interaction | None:
        """Locate interaction whose choices contain this callback_data."""
        # Fast path: scan recent pending — token is in choices JSON
        stmt = (
            select(Interaction)
            .where(Interaction.status.in_(["pending", "consumed", "expired"]))
            .order_by(Interaction.created_at.desc())
            .limit(200)
        )
        if principal_id is not None:
            stmt = stmt.where(Interaction.principal_id == principal_id)
        result = await self.session.execute(stmt)
        for ix in result.scalars().all():
            for c in ix.choices or []:
                if c.get("callback_data") == callback_data:
                    return ix
            if ix.callback_token == callback_data:
                return ix
        return None

    async def consume(
        self,
        interaction: Interaction,
        *,
        choice_id: str | None,
        principal_id,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Atomically consume. Returns status outcome for webhook/worker."""
        now = now or datetime.now(timezone.utc)
        if interaction.principal_id != principal_id:
            logger.warning(
                "interactive_wrong_principal",
                interaction_id=str(interaction.id),
            )
            return {"ok": False, "status": "forbidden"}

        # Re-read with row feel via flush path — status checks
        if interaction.status == "consumed":
            logger.info(
                "interactive_replay_rejected",
                interaction_id=str(interaction.id),
            )
            return {
                "ok": False,
                "status": "already_consumed",
                "choice_id": interaction.consumed_choice_id,
            }
        if interaction.status == "expired" or (
            interaction.expires_at is not None and interaction.expires_at <= now
        ):
            if interaction.status != "expired":
                interaction.status = "expired"
                await self.session.flush()
            logger.info(
                "interactive_expired",
                interaction_id=str(interaction.id),
            )
            return {"ok": False, "status": "expired"}
        if interaction.status != "pending":
            return {"ok": False, "status": interaction.status}

        interaction.status = "consumed"
        interaction.consumed_at = now
        interaction.consumed_choice_id = choice_id
        await self.session.flush()
        logger.info(
            "interactive_consumed",
            interaction_id=str(interaction.id),
            choice_id=choice_id,
            principal_id=str(principal_id),
        )
        return {
            "ok": True,
            "status": "consumed",
            "interaction_id": str(interaction.id),
            "choice_id": choice_id,
            "choices": interaction.choices,
            "activity_id": str(interaction.activity_id) if interaction.activity_id else None,
            "work_id": str(interaction.work_id) if interaction.work_id else None,
        }

    async def expire_due(self, limit: int = 50) -> list[Interaction]:
        """Mark pending interactions past expires_at as expired. Returns them."""
        now = datetime.now(timezone.utc)
        stmt = (
            select(Interaction)
            .where(
                Interaction.status == "pending",
                Interaction.expires_at.is_not(None),
                Interaction.expires_at <= now,
            )
            .order_by(Interaction.expires_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self.session.execute(stmt)
        rows = list(result.scalars().all())
        for ix in rows:
            ix.status = "expired"
            logger.info(
                "interactive_expired",
                interaction_id=str(ix.id),
                principal_id=str(ix.principal_id),
            )
        if rows:
            await self.session.flush()
        return rows

    async def enqueue_continuation(
        self,
        interaction: Interaction,
        *,
        reason: str,
        choice_id: str | None = None,
        text: str | None = None,
    ) -> Work:
        """Create Work so the tutor can react to consume or timeout."""
        payload = {
            "channel": interaction.channel,
            "interaction_id": str(interaction.id),
            "interaction_status": interaction.status,
            "choice_id": choice_id,
            "text": text
            or (f"[interaction:{reason}:{choice_id or 'none'}]"),
            "activity_id": str(interaction.activity_id) if interaction.activity_id else None,
            "source": "interaction",
            "reason": reason,
        }
        # Prefer target from metadata if stored
        meta = interaction.metadata_ or {}
        if meta.get("target_external_id"):
            payload["target_external_id"] = meta["target_external_id"]

        work = Work(
            id=uuid4(),
            principal_id=interaction.principal_id,
            conversation_id=interaction.conversation_id,
            kind="message_response",
            status="queued",
            priority=40,
            objective=f"interaction_{reason}",
            input_payload=payload,
        )
        self.session.add(work)
        await self.session.flush()
        logger.info(
            "interaction_continuation_enqueued",
            interaction_id=str(interaction.id),
            work_id=str(work.id),
            reason=reason,
        )
        return work
