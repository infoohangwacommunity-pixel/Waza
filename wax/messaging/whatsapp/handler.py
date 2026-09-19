"""WhatsApp webhook — accept fast, create durable work, return."""

from __future__ import annotations

import hashlib
import hmac
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
from wax.observability.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


def _verify_signature(body: bytes, signature_header: str | None) -> bool:
    if not settings.whatsapp_app_secret:
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        settings.whatsapp_app_secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header[7:])


async def handle_whatsapp_webhook(body: bytes, headers: dict[str, str]) -> dict[str, Any]:
    if settings.webhook_signature_required and not _verify_signature(
        body, headers.get("x-hub-signature-256")
    ):
        logger.warning("whatsapp_invalid_signature")
        return {"status": "invalid_signature"}

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return {"status": "invalid_json"}

    processed = 0
    async with session_scope() as session:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                contacts = {c.get("wa_id"): c for c in value.get("contacts", [])}
                for msg in value.get("messages", []):
                    external_id = msg.get("id")
                    if not external_id:
                        continue

                    stmt = (
                        insert(InboundEvent)
                        .values(
                            id=uuid.uuid4(),
                            channel="whatsapp",
                            external_event_id=external_id,
                            event_type=msg.get("type", "text"),
                            payload=msg,
                            processed=False,
                        )
                        .on_conflict_do_nothing(index_elements=["channel", "external_event_id"])
                        .returning(InboundEvent.id)
                    )
                    result = await session.execute(stmt)
                    inserted = result.scalar_one_or_none()
                    if not inserted:
                        logger.info("whatsapp_duplicate_ignored", external_id=external_id)
                        continue

                    wa_id = msg.get("from")
                    principal, _ = await _resolve_identity(session, wa_id, contacts.get(wa_id))
                    conversation = await _get_or_create_conversation(session, principal.id, "whatsapp")

                    text = ""
                    if msg.get("type") == "text":
                        text = msg.get("text", {}).get("body", "")
                    else:
                        text = f"[{msg.get('type')} message]"

                    message = Message(
                        id=uuid.uuid4(),
                        conversation_id=conversation.id,
                        principal_id=principal.id,
                        channel="whatsapp",
                        direction="inbound",
                        role="user",
                        content=text,
                        external_id=external_id,
                        metadata_={"raw": msg},
                    )
                    session.add(message)

                    work = Work(
                        id=uuid.uuid4(),
                        principal_id=principal.id,
                        conversation_id=conversation.id,
                        kind="message_response",
                        status="queued",
                        priority=50,
                        objective="Respond to inbound WhatsApp message",
                        input_payload={
                            "channel": "whatsapp",
                            "message_id": str(message.id),
                            "external_id": external_id,
                            "text": text,
                            "target_external_id": wa_id,
                        },
                    )
                    session.add(work)

                    event = await session.get(InboundEvent, inserted)
                    if event:
                        event.processed = True
                        event.work_id = work.id
                    processed += 1

    return {"status": "ok", "processed": processed}


async def _resolve_identity(session, wa_id: str | None, contact: dict | None):
    if not wa_id:
        raise ValueError("missing wa_id")
    stmt = select(InterfaceIdentity).where(
        InterfaceIdentity.channel == "whatsapp",
        InterfaceIdentity.external_id == wa_id,
    )
    result = await session.execute(stmt)
    identity = result.scalar_one_or_none()
    if identity:
        principal = await session.get(Principal, identity.principal_id)
        return principal, identity

    principal = Principal(
        id=uuid.uuid4(),
        display_name=(contact or {}).get("profile", {}).get("name"),
    )
    session.add(principal)
    await session.flush()
    identity = InterfaceIdentity(
        id=uuid.uuid4(),
        principal_id=principal.id,
        channel="whatsapp",
        external_id=wa_id,
        display_name=principal.display_name,
        is_primary=True,
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
    conv = Conversation(id=uuid.uuid4(), principal_id=principal_id, channel=channel, status="active")
    session.add(conv)
    await session.flush()
    return conv
