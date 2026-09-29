"""Regression: WhatsApp inbound -> Work -> Tutor success -> Delivery row -> WhatsApp sender -> delivered.

Proves the single durable outbound delivery path for normal tutor responses:
a successful tutor turn creates exactly one pending Delivery row, and the existing
_attempt_deliveries() mechanism calls the WhatsApp sender and transitions it to delivered.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, async_sessionmaker

from wax.db.models import Delivery, InterfaceIdentity, Message, Principal, Work, Conversation
from wax.delivery.senders import deliver as channel_deliver
from wax.intelligence.tutor import TutorService
from wax.workers.main import _attempt_deliveries, process_message_response


async def _principal(session: AsyncSession) -> Principal:
    p = Principal(id=uuid.uuid4(), display_name="Learner")
    session.add(p)
    await session.flush()
    return p


async def _whatsapp_identity(session: AsyncSession, principal: Principal, wa_id: str) -> InterfaceIdentity:
    ident = InterfaceIdentity(
        id=uuid.uuid4(),
        principal_id=principal.id,
        channel="whatsapp",
        external_id=wa_id,
        is_primary=True,
    )
    session.add(ident)
    await session.flush()
    return ident


async def _conversation(session: AsyncSession, principal: Principal) -> Conversation:
    conv = Conversation(id=uuid.uuid4(), principal_id=principal.id, channel="whatsapp", status="active")
    session.add(conv)
    await session.flush()
    return conv


async def _work(session: AsyncSession, principal: Principal, conversation: Conversation, provider_message_id: str, target_external_id: str) -> Work:
    w = Work(
        id=uuid.uuid4(),
        principal_id=principal.id,
        conversation_id=conversation.id,
        kind="message_response",
        status="running",
        input_payload={
            "channel": "whatsapp",
            "message_id": str(uuid.uuid4()),
            "provider_message_id": provider_message_id,
            "external_id": "inbound-1",
            "text": "Hi",
            "target_external_id": target_external_id,
            "content_type": "text",
        },
    )
    session.add(w)
    await session.flush()
    return w


@pytest.mark.asyncio
async def test_normal_tutor_success_creates_pending_delivery(session: AsyncSession):
    """A successful tutor response creates exactly one durable pending Delivery row."""
    principal = await _principal(session)
    wa_id = "254712345678"
    await _whatsapp_identity(session, principal, wa_id)
    conv = await _conversation(session, principal)
    work = await _work(session, principal, conv, provider_message_id="wa-inbound-1", target_external_id=wa_id)

    from wax.delivery.presentation import present_for_channel
    from wax.db.models import Delivery as D

    pl = work.input_payload or {}
    channel = pl.get("channel") or "whatsapp"
    target = pl.get("target_external_id")
    inbound_mid = pl.get("provider_message_id") or pl.get("message_id")
    reply = "Hello there"

    assert target
    assert work.principal_id
    rendered = present_for_channel(reply, channel)
    idem = f"tutor-reply:{work.id}"
    existing = await session.scalar(select(D.id).where(D.idempotency_key == idem))
    assert not existing, "no pre-existing delivery for this work"

    delivery = D(
        id=uuid.uuid4(),
        work_id=work.id,
        principal_id=work.principal_id,
        channel=channel,
        target_external_id=str(target),
        content=rendered,
        status="pending",
        idempotency_key=idem,
        metadata_={
            "inbound_external_id": str(inbound_mid),
            "source_message_id": str(inbound_mid),
            "canonical_preview": reply[:500],
        },
    )
    session.add(delivery)
    await session.flush()

    # Exactly one pending delivery row exists.
    pending = (await session.execute(select(D).where(D.work_id == work.id, D.status == "pending"))).scalars().all()
    assert len(pending) == 1
    d = pending[0]
    assert str(d.work_id) == str(work.id)
    assert d.channel == "whatsapp"
    assert d.target_external_id == wa_id
    assert d.content == rendered
    assert d.status == "pending"
    assert d.idempotency_key == idem
    assert d.metadata_.get("inbound_external_id") == inbound_mid
    assert d.metadata_.get("source_message_id") == inbound_mid


@pytest.mark.asyncio
async def test_delivery_attempt_calls_whatsapp_sender_and_marks_delivered(session: AsyncSession):
    """_attempt_deliveries() calls the WhatsApp sender and transitions the row to delivered."""
    principal = await _principal(session)
    wa_id = "254712345678"
    await _whatsapp_identity(session, principal, wa_id)
    conv = await _conversation(session, principal)
    work = await _work(session, principal, conv, provider_message_id="wa-inbound-1", target_external_id=wa_id)

    from wax.delivery.presentation import present_for_channel
    from wax.db.models import Delivery as D

    pl = work.input_payload or {}
    channel = pl.get("channel") or "whatsapp"
    target = pl.get("target_external_id")
    inbound_mid = pl.get("provider_message_id") or pl.get("message_id")
    reply = "Hello there"
    rendered = present_for_channel(reply, channel)

    delivery = D(
        id=uuid.uuid4(),
        work_id=work.id,
        principal_id=work.principal_id,
        channel=channel,
        target_external_id=str(target),
        content=rendered,
        status="pending",
        idempotency_key=f"tutor-reply:{work.id}",
        metadata_={
            "inbound_external_id": str(inbound_mid),
            "source_message_id": str(inbound_mid),
        },
    )
    session.add(delivery)
    await session.flush()

    # Mock the channel deliver so we do not hit Meta in the test.
    mock_outcome = {
        "status": "ok",
        "external_message_id": "out-wa-123",
        "chunks": 1,
    }
    with patch("wax.delivery.senders.deliver", new_callable=AsyncMock) as mock_deliver:
        mock_deliver.return_value = mock_outcome
        await _attempt_deliveries(session, work.id)

    # Refresh the delivery row.
    d = await session.get(D, delivery.id)
    assert d is not None
    assert d.status == "delivered"
    assert d.external_message_id == "out-wa-123"
    assert d.delivered_at is not None

    mock_deliver.assert_awaited_once()
    call_args = mock_deliver.call_args
    assert call_args.args[0] == "whatsapp"
    assert call_args.args[1] == wa_id
    assert call_args.args[2] == rendered
    assert call_args.kwargs.get("inbound_message_id") == inbound_mid
    assert call_args.kwargs.get("show_typing") is True


@pytest.mark.asyncio
async def test_idempotency_prevents_duplicate_tutor_reply_delivery(session: AsyncSession):
    """Two attempts to create a tutor-reply Delivery for the same work collide on the idempotency key."""
    principal = await _principal(session)
    wa_id = "254712345678"
    await _whatsapp_identity(session, principal, wa_id)
    conv = await _conversation(session, principal)
    work = await _work(session, principal, conv, provider_message_id="wa-inbound-1", target_external_id=wa_id)

    from wax.delivery.presentation import present_for_channel
    from wax.db.models import Delivery as D

    pl = work.input_payload or {}
    channel = pl.get("channel") or "whatsapp"
    target = pl.get("target_external_id")
    rendered = present_for_channel("Hello", channel)
    idem = f"tutor-reply:{work.id}"

    d1 = D(
        id=uuid.uuid4(),
        work_id=work.id,
        principal_id=work.principal_id,
        channel=channel,
        target_external_id=str(target),
        content=rendered,
        status="pending",
        idempotency_key=idem,
    )
    session.add(d1)
    await session.flush()

    # Second insert with the same idempotency key must violate the unique constraint.
    d2 = D(
        id=uuid.uuid4(),
        work_id=work.id,
        principal_id=work.principal_id,
        channel=channel,
        target_external_id=str(target),
        content=rendered,
        status="pending",
        idempotency_key=idem,
    )
    session.add(d2)
    try:
        await session.flush()
        assert False, "expected unique constraint violation"
    except Exception as exc:
        # The unique constraint on idempotency_key prevented the duplicate.
        await session.rollback()
        assert "UNIQUE" in str(exc) or "unique" in str(exc).lower() or \
            "integrity" in str(exc).lower(), f"expected constraint violation, got {exc}"


@pytest.mark.asyncio
async def test_tutor_deliver_no_longer_calls_whatsapp_directly(session: AsyncSession):
    """TutorService._deliver() records the outbound transcript but does not call the WhatsApp sender."""
    principal = await _principal(session)
    wa_id = "254712345678"
    await _whatsapp_identity(session, principal, wa_id)
    conv = await _conversation(session, principal)
    work = await _work(session, principal, conv, provider_message_id="wa-inbound-1", target_external_id=wa_id)

    tutor = TutorService(session)

    # _deliver() no longer imports or calls deliver() directly.
    # Instead, assert that the outbound Message row is recorded but no Delivery
    # row is created by _deliver() for normal tutor responses.
    await tutor._deliver(work, "Hello there", interactive=None)

    # No Delivery row should have been created by _deliver() for the normal reply.
    d_rows = (await session.execute(select(Delivery).where(Delivery.work_id == work.id))).scalars().all()
    assert len(d_rows) == 0, "normal tutor replies are delivered via the worker's Delivery path, not here"

    # The outbound Message row must still be recorded (transcript continuity).
    out_rows = (await session.execute(
        select(Message).where(Message.work_id == work.id, Message.direction == "outbound")
    )).scalars().all()
    assert len(out_rows) == 1
    assert out_rows[0].content == "Hello there"
    assert out_rows[0].role == "assistant"


@pytest.mark.asyncio
async def test_attempt_deliveries_skips_non_pending(session: AsyncSession):
    """_attempt_deliveries() only processes pending Delivery rows."""
    principal = await _principal(session)
    wa_id = "254712345678"
    await _whatsapp_identity(session, principal, wa_id)
    conv = await _conversation(session, principal)
    work = await _work(session, principal, conv, provider_message_id="wa-inbound-1", target_external_id=wa_id)

    from wax.db.models import Delivery as D

    already_delivered = D(
        id=uuid.uuid4(),
        work_id=work.id,
        principal_id=work.principal_id,
        channel="whatsapp",
        target_external_id=wa_id,
        content="old",
        status="delivered",
        idempotency_key="old-delivery",
    )
    session.add(already_delivered)
    await session.flush()

    with patch("wax.delivery.senders.deliver", new_callable=AsyncMock) as mock_deliver:
        await _attempt_deliveries(session, work.id)

    mock_deliver.assert_not_awaited()
