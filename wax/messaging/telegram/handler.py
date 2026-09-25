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
        from wax.security.webhooks import verify_telegram_secret

        token = headers.get("x-telegram-bot-api-secret-token")
        if not verify_telegram_secret(settings.telegram_webhook_secret, token):
            logger.warning("telegram_invalid_secret")
            raise WebhookAcceptError("invalid_secret", 403)

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise WebhookAcceptError("invalid_json", 400)

    # Inline button callbacks — durable Interaction consume path
    if payload.get("callback_query"):
        return await _handle_telegram_callback(payload)

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
            # autoflush=False: INSERT works before assigning inbound_events.work_id
            await session.flush()

            persisted = await session.get(Work, work.id)
            if persisted is None:
                logger.error(
                    "telegram_work_missing_after_flush",
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
                    "telegram_accept_linked",
                    inbound_event_id=str(inserted),
                    work_id=str(work.id),
                    principal_id=str(principal.id),
                    external_id=external_id,
                )
    except WebhookAcceptError:
        raise
    except Exception as e:
        logger.exception("telegram_accept_failed")
        raise WebhookAcceptError("persistence_failed", 503) from e

    return {"status": "ok"}


async def _handle_telegram_callback(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Durable Interaction consume path for Telegram inline buttons.

    Flow:
      callback_query → idempotent InboundEvent → find Interaction → consume once
      → enqueue continuation Work → answerCallbackQuery (stop spinner).

    Does not run AI or delivery inside the accept transaction.
    """
    cq = payload.get("callback_query") or {}
    callback_id = str(cq.get("id") or "")
    data = str(cq.get("data") or "").strip()
    from_user = cq.get("from") or {}
    message = cq.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or from_user.get("id") or "")
    msg_id = message.get("message_id")

    if not callback_id or not data or not chat_id:
        logger.warning(
            "telegram_callback_incomplete",
            has_id=bool(callback_id),
            has_data=bool(data),
            has_chat=bool(chat_id),
        )
        # Still try to clear the spinner when possible
        if callback_id:
            try:
                from wax.messaging.telegram.client import answer_callback_query

                await answer_callback_query(callback_id, text="Unavailable")
            except Exception:
                pass
        return {"status": "ignored"}

    # Unique external id for idempotency (Telegram may retry the same callback)
    external_event_id = f"tg_cb:{callback_id}"

    try:
        async with session_scope() as session:
            stmt = (
                insert(InboundEvent)
                .values(
                    id=uuid.uuid4(),
                    channel="telegram",
                    external_event_id=external_event_id,
                    event_type="callback_query",
                    payload=cq,
                    processed=False,
                )
                .on_conflict_do_nothing(index_elements=["channel", "external_event_id"])
                .returning(InboundEvent.id)
            )
            result = await session.execute(stmt)
            inserted = result.scalar_one_or_none()
            if not inserted:
                # Duplicate delivery of the same callback — acknowledge and stop
                try:
                    from wax.messaging.telegram.client import answer_callback_query

                    await answer_callback_query(callback_id)
                except Exception:
                    pass
                return {"status": "duplicate"}

            principal, _ = await _resolve_identity(session, chat_id, from_user)
            conversation = await _get_or_create_conversation(
                session, principal.id, "telegram"
            )

            from wax.interaction.service import InteractionService

            isvc = InteractionService(session)
            ix = await isvc.find_by_callback(data, principal_id=principal.id)

            if ix is None:
                event = await session.get(InboundEvent, inserted)
                if event:
                    event.processed = True
                await session.flush()
                try:
                    from wax.messaging.telegram.client import answer_callback_query

                    await answer_callback_query(callback_id, text="That option is no longer available.")
                except Exception:
                    pass
                logger.info(
                    "telegram_callback_unknown_interaction",
                    callback_data=data[:64],
                    principal_id=str(principal.id),
                )
                return {"status": "ok", "detail": "unknown_interaction"}

            choice_id = None
            choice_title = None
            for c in ix.choices or []:
                if c.get("callback_data") == data or c.get("id") == data:
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
                await session.flush()
                try:
                    from wax.messaging.telegram.client import answer_callback_query

                    await answer_callback_query(callback_id, text="Already selected.")
                except Exception:
                    pass
                return {"status": "ok", "detail": "already_consumed"}

            if outcome.get("status") == "expired":
                work = await isvc.enqueue_continuation(
                    ix, reason="expired_on_tap", choice_id=choice_id
                )
                pl = dict(work.input_payload or {})
                pl["target_external_id"] = chat_id
                pl["channel"] = "telegram"
                if msg_id is not None:
                    pl["source_message_id"] = str(msg_id)
                work.input_payload = pl
                if event:
                    event.work_id = work.id
                await session.flush()
                try:
                    from wax.messaging.telegram.client import answer_callback_query

                    await answer_callback_query(callback_id, text="That option expired.")
                except Exception:
                    pass
                return {"status": "ok", "detail": "expired"}

            if outcome.get("ok"):
                text_choice = choice_title or choice_id or data
                work = await isvc.enqueue_continuation(
                    ix,
                    reason="choice",
                    choice_id=choice_id,
                    text=text_choice,
                )
                pl = dict(work.input_payload or {})
                pl["target_external_id"] = chat_id
                pl["channel"] = "telegram"
                if msg_id is not None:
                    pl["source_message_id"] = str(msg_id)
                work.input_payload = pl
                if event:
                    event.work_id = work.id

                # User-visible message so conversation history has the choice
                msg = Message(
                    id=uuid.uuid4(),
                    conversation_id=conversation.id,
                    principal_id=principal.id,
                    channel="telegram",
                    direction="inbound",
                    role="user",
                    content=text_choice,
                    external_id=external_event_id,
                    metadata_={
                        "content_type": "interactive",
                        "interactive_id": data,
                        "interaction_id": str(ix.id),
                        "choice_id": choice_id,
                    },
                )
                session.add(msg)
                await session.flush()

                try:
                    from wax.messaging.telegram.client import (
                        answer_callback_query,
                        clear_inline_keyboard,
                    )

                    await answer_callback_query(callback_id)
                    if msg_id is not None:
                        await clear_inline_keyboard(chat_id, msg_id)
                except Exception:
                    logger.exception("telegram_callback_ack_failed")

                logger.info(
                    "telegram_callback_consumed",
                    interaction_id=str(ix.id),
                    work_id=str(work.id),
                    choice_id=choice_id,
                    principal_id=str(principal.id),
                )
                return {"status": "ok", "detail": "consumed"}

            # Forbidden / other status — mark processed, no Work
            await session.flush()
            try:
                from wax.messaging.telegram.client import answer_callback_query

                await answer_callback_query(callback_id, text="Unable to use that option.")
            except Exception:
                pass
            return {"status": "ok", "detail": outcome.get("status") or "rejected"}

    except WebhookAcceptError:
        raise
    except Exception as e:
        logger.exception("telegram_callback_accept_failed")
        try:
            from wax.messaging.telegram.client import answer_callback_query

            await answer_callback_query(callback_id, text="Something went wrong. Try again.")
        except Exception:
            pass
        raise WebhookAcceptError("persistence_failed", 503) from e


async def _resolve_identity(session, chat_id: str, from_user: dict):
    """Canonical path: existing InterfaceIdentity → same Principal (no second learner)."""
    from wax.domain.identity import resolve_or_create_messaging_identity

    name = from_user.get("first_name") or from_user.get("username")
    return await resolve_or_create_messaging_identity(
        session,
        channel="telegram",
        external_id=str(chat_id),
        display_name=name,
        metadata={"username": from_user.get("username")} if from_user else None,
    )


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
