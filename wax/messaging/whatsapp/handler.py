"""WhatsApp webhook — accept only. No AI, media download, or delivery inside the DB transaction."""

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
from wax.messaging.normalization import normalize_whatsapp_message
from wax.observability.logging import get_logger
from wax.security.webhooks import verify_whatsapp_signature

logger = get_logger(__name__)
settings = get_settings()


class WebhookAcceptError(Exception):
    """Raised when the event could not be durably accepted."""

    def __init__(self, status: str, http_status: int = 500):
        self.status = status
        self.http_status = http_status
        super().__init__(status)


def _verify_signature(body: bytes, signature_header: str | None) -> bool:
    return verify_whatsapp_signature(
        settings.whatsapp_app_secret,
        body,
        signature_header,
        required=bool(getattr(settings, "webhook_signature_required", True)),
    )


async def handle_whatsapp_webhook(body: bytes, headers: dict[str, str]) -> dict[str, Any]:
    """
    RECEIVE → verify → parse → normalize → atomic dedupe → persist → Work → COMMIT.
    Returns status dict. Raises WebhookAcceptError if durability fails.
    """
    if settings.webhook_signature_required and not _verify_signature(
        body, headers.get("x-hub-signature-256")
    ):
        logger.warning("whatsapp_invalid_signature")
        raise WebhookAcceptError("invalid_signature", 403)

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise WebhookAcceptError("invalid_json", 400)

    processed = 0
    duplicates = 0
    try:
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
                            .on_conflict_do_nothing(
                                index_elements=["channel", "external_event_id"]
                            )
                            .returning(InboundEvent.id)
                        )
                        result = await session.execute(stmt)
                        inserted = result.scalar_one_or_none()
                        if not inserted:
                            duplicates += 1
                            logger.info("whatsapp_duplicate_ignored", external_id=external_id)
                            continue

                        wa_id = msg.get("from")
                        principal, _ = await _resolve_identity(
                            session, wa_id, contacts.get(wa_id)
                        )
                        conversation = await _get_or_create_conversation(
                            session, principal.id, "whatsapp"
                        )

                        normalized = normalize_whatsapp_message(msg, contacts)
                        if normalized:
                            text = normalized.text
                            content_type = normalized.content_type
                            media_id = normalized.media_id
                            interactive_id = normalized.interactive_id
                        else:
                            content_type = msg.get("type") or "text"
                            media_id = None
                            interactive_id = None
                            if content_type == "text":
                                text = (msg.get("text") or {}).get("body", "")
                            else:
                                text = f"[{content_type} message]"

                        # Durable interaction consume (button / list reply)
                        if interactive_id:
                            from wax.interaction.service import InteractionService
                            isvc = InteractionService(session)
                            ix = await isvc.find_by_callback(
                                interactive_id, principal_id=principal.id
                            )
                            if ix is not None:
                                choice_id = None
                                choice_title = None
                                for c in ix.choices or []:
                                    if c.get("callback_data") == interactive_id or c.get("id") == interactive_id:
                                        choice_id = c.get("logical_id") or c.get("id")
                                        choice_title = c.get("title")
                                        break
                                outcome = await isvc.consume(
                                    ix, choice_id=choice_id, principal_id=principal.id
                                )
                                event = await session.get(InboundEvent, inserted)
                                if event:
                                    event.processed = True
                                if outcome.get("status") == "already_consumed":
                                    processed += 1
                                    continue
                                if outcome.get("status") == "expired":
                                    work = await isvc.enqueue_continuation(
                                        ix, reason="expired_on_tap", choice_id=choice_id
                                    )
                                    pl = dict(work.input_payload or {})
                                    pl["target_external_id"] = wa_id
                                    pl["channel"] = "whatsapp"
                                    work.input_payload = pl
                                    if event:
                                        event.work_id = work.id
                                    processed += 1
                                    continue
                                if outcome.get("ok"):
                                    text_choice = choice_title or choice_id or interactive_id
                                    work = await isvc.enqueue_continuation(
                                        ix,
                                        reason="choice",
                                        choice_id=choice_id,
                                        text=text_choice,
                                    )
                                    pl = dict(work.input_payload or {})
                                    pl["target_external_id"] = wa_id
                                    pl["channel"] = "whatsapp"
                                    work.input_payload = pl
                                    if event:
                                        event.work_id = work.id
                                    await session.flush()
                                    processed += 1
                                    continue
                                # forbidden / other → fall through to normal message path

                        # Media id recorded only — worker downloads asynchronously
                        message = Message(
                            id=uuid.uuid4(),
                            conversation_id=conversation.id,
                            principal_id=principal.id,
                            channel="whatsapp",
                            direction="inbound",
                            role="user",
                            content=text,
                            external_id=external_id,
                            metadata_={
                                "content_type": content_type,
                                "media_id": media_id,
                                "interactive_id": interactive_id,
                            },
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
                                "content_type": content_type,
                                "media_id": media_id,
                                "interactive_id": interactive_id,
                            },
                        )
                        session.add(work)
                        # autoflush=False: INSERT works before assigning inbound_events.work_id
                        await session.flush()

                        persisted = await session.get(Work, work.id)
                        if persisted is None:
                            logger.error(
                                "whatsapp_work_missing_after_flush",
                                work_id=str(work.id),
                                inbound_event_id=str(inserted),
                                principal_id=str(principal.id),
                            )
                            raise WebhookAcceptError("work_persist_failed", 503)

                        event = await session.get(InboundEvent, inserted)
                        if event:
                            event.processed = True
                            event.work_id = work.id
                            logger.info(
                                "whatsapp_accept_linked",
                                inbound_event_id=str(inserted),
                                work_id=str(work.id),
                                principal_id=str(principal.id),
                                external_id=external_id,
                            )
                        processed += 1
            # session_scope commits on exit
    except WebhookAcceptError:
        raise
    except Exception as e:
        logger.exception("whatsapp_accept_failed")
        raise WebhookAcceptError("persistence_failed", 503) from e

    return {
        "status": "ok",
        "processed": processed,
        "duplicates": duplicates,
    }


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

    name = None
    if contact:
        name = (contact.get("profile") or {}).get("name")
    principal = Principal(id=uuid.uuid4(), display_name=name)
    session.add(principal)
    await session.flush()
    identity = InterfaceIdentity(
        id=uuid.uuid4(),
        principal_id=principal.id,
        channel="whatsapp",
        external_id=wa_id,
        display_name=name,
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
    conv = Conversation(
        id=uuid.uuid4(), principal_id=principal_id, channel=channel, status="active"
    )
    session.add(conv)
    await session.flush()
    return conv
