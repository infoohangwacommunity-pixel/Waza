"""Telegram webhook — accept only. No AI, media download, or typing inside DB transaction."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from wax.config import get_settings
from wax.db.models import (
    Conversation,
    InboundEvent,
    InterfaceIdentity,
    Message,
    Principal,
    Work,
)
from wax.db.session import session_scope
from wax.messaging.normalization import normalize_telegram_update
from wax.observability.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


class WebhookAcceptError(Exception):
    def __init__(self, status: str, http_status: int = 500):
        self.status = status
        self.http_status = http_status
        super().__init__(status)


async def handle_telegram_webhook(body: bytes, headers: dict[str, str]) -> dict[str, Any]:
    if settings.telegram_webhook_secret:
        token = headers.get("x-telegram-bot-api-secret-token")
        if token != settings.telegram_webhook_secret:
            logger.warning("telegram_invalid_secret")
            raise WebhookAcceptError("invalid_secret", 403)

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise WebhookAcceptError("invalid_json", 400)

    normalized = normalize_telegram_update(payload)
    if not normalized:
        return {"status": "ignored"}

    external_id = normalized.external_event_id
    chat_id = normalized.external_user_id
    text = normalized.text
    content_type = normalized.content_type
    media_id = normalized.media_id

    try:
        async with session_scope() as session:
            stmt = (
                insert(InboundEvent)
                .values(
                    id=uuid.uuid4(),
                    channel="telegram",
                    external_event_id=external_id,
                    event_type="message",
                    payload=payload.get("message") or payload,
                    processed=False,
                )
                .on_conflict_do_nothing(index_elements=["channel", "external_event_id"])
                .returning(InboundEvent.id)
            )
            result = await session.execute(stmt)
            inserted = result.scalar_one_or_none()
            if not inserted:
                return {"status": "duplicate"}

            from_user = (payload.get("message") or {}).get("from") or {}
            principal, _ = await _resolve_identity(session, chat_id, from_user)
            conversation = await _get_or_create_conversation(
                session, principal.id, "telegram"
            )

            msg = Message(
                id=uuid.uuid4(),
                conversation_id=conversation.id,
                principal_id=principal.id,
                channel="telegram",
                direction="inbound",
                role="user",
                content=text,
                external_id=external_id,
                metadata_={
                    "content_type": content_type,
                    "media_id": media_id,
                },
            )
            session.add(msg)

            work = Work(
                id=uuid.uuid4(),
                principal_id=principal.id,
                conversation_id=conversation.id,
                kind="message_response",
                status="queued",
                priority=50,
                objective="Respond to inbound Telegram message",
                input_payload={
                    "channel": "telegram",
                    "message_id": str(msg.id),
                    "external_id": external_id,
                    "text": text,
                    "target_external_id": chat_id,
                    "content_type": content_type,
                    "media_id": media_id,
                },
            )
            session.add(work)

            event = await session.get(InboundEvent, inserted)
            if event:
                event.processed = True
                event.work_id = work.id
    except WebhookAcceptError:
        raise
    except Exception as e:
        logger.exception("telegram_accept_failed")
        raise WebhookAcceptError("persistence_failed", 503) from e

    return {"status": "ok"}


async def _resolve_identity(session, chat_id: str, from_user: dict):
    stmt = select(InterfaceIdentity).where(
        InterfaceIdentity.channel == "telegram",
        InterfaceIdentity.external_id == chat_id,
    )
    result = await session.execute(stmt)
    identity = result.scalar_one_or_none()
    if identity:
        principal = await session.get(Principal, identity.principal_id)
        return principal, identity

    name = from_user.get("first_name") or from_user.get("username")
    principal = Principal(id=uuid.uuid4(), display_name=name)
    session.add(principal)
    await session.flush()
    identity = InterfaceIdentity(
        id=uuid.uuid4(),
        principal_id=principal.id,
        channel="telegram",
        external_id=chat_id,
        display_name=name,
        is_primary=True,
        metadata_={"username": from_user.get("username")},
    )
    session.add(identity)
    await session.flush()
    return principal, identity


async def _get_or_create_conversation(session, principal_id, channel: str):
    stmt = (
        select(Conversation)
        .where(
            Conversation.principal_id == principal_id,
            Conversation.channel == channel,
            Conversation.status == "active",
        )
        .order_by(Conversation.updated_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    conv = result.scalar_one_or_none()
    if conv:
        return conv
    conv = Conversation(
        id=uuid.uuid4(), principal_id=principal_id, channel=channel, status="active"
    )
    session.add(conv)
    await session.flush()
    return conv
