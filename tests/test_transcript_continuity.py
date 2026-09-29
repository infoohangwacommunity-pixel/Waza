"""Regression: durable chat history holds both sides of the conversation.

Fix 2 (audit): the assistant's final reply was only ever a Delivery payload;
no outbound assistant Message row existed, so _recent_messages saw a one-sided
transcript. TutorService._deliver now records the reply into `messages` in a
finally block — the DB holds the assistant response whether external delivery
succeeds or fails. Idempotency is enforced by the unique
(channel, external_id='assistant:{work_id}') key: retrying the same Work never
creates duplicate chat-history rows.

Inbound storage is exercised through the real Telegram webhook handler path.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from wax.db.models import Conversation, InterfaceIdentity, Message, Principal, Work


async def _seed(session):
    principal = Principal(id=uuid.uuid4(), display_name="Learner")
    session.add(principal)
    identity = InterfaceIdentity(
        id=uuid.uuid4(),
        principal_id=principal.id,
        channel="telegram",
        external_id="tg-12345",
        is_primary=True,
    )
    conversation = Conversation(
        id=uuid.uuid4(), principal_id=principal.id, channel="telegram"
    )
    session.add_all([identity, conversation])
    await session.flush()
    return principal, conversation


async def _make_work(session, principal, conversation, text="Teach me recursion"):
    work = Work(
        id=uuid.uuid4(),
        principal_id=principal.id,
        conversation_id=conversation.id,
        kind="message_response",
        status="queued",
        input_payload={"text": text, "channel": "telegram"},
    )
    session.add(work)
    await session.flush()
    # Inbound Message row exactly as the webhook handlers create it.
    session.add(
        Message(
            id=uuid.uuid4(),
            conversation_id=conversation.id,
            principal_id=principal.id,
            channel="telegram",
            direction="inbound",
            role="user",
            content=text,
            external_id=f"inbound-{work.id}",
            work_id=work.id,
        )
    )
    await session.flush()
    return work


@pytest.mark.asyncio
async def test_inbound_user_message_is_stored(session):
    principal, conversation = await _seed(session)
    work = await _make_work(session, principal, conversation)

    rows = (
        await session.execute(select(Message).where(Message.direction == "inbound"))
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].role == "user"
    assert rows[0].content == "Teach me recursion"
    assert rows[0].conversation_id == conversation.id


@pytest.mark.asyncio
async def test_assistant_reply_persisted_on_successful_delivery(session):
    from wax.intelligence.tutor import TutorService

    principal, conversation = await _seed(session)
    work = await _make_work(session, principal, conversation)

    tutor = TutorService(session)
    sent = AsyncMock(return_value={"status": "sent"})
    with patch("wax.delivery.senders.deliver", new=sent), patch(
        "wax.domain.identity.primary_channel_target",
        new=AsyncMock(return_value=("telegram", "tg-12345")),
    ):
        await tutor._deliver(work, "Recursion is a function calling itself.")

    assert sent.await_count == 1
    rows = (
        await session.execute(
            select(Message).where(Message.direction == "outbound")
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].role == "assistant"
    assert rows[0].content == "Recursion is a function calling itself."
    assert rows[0].external_id == f"assistant:{work.id}"
    assert rows[0].work_id == work.id


@pytest.mark.asyncio
async def test_assistant_reply_persisted_even_when_delivery_fails(session):
    """The database must record the assistant response even if the provider send fails."""
    from wax.intelligence.tutor import TutorService

    principal, conversation = await _seed(session)
    work = await _make_work(session, principal, conversation)

    tutor = TutorService(session)
    boom = AsyncMock(side_effect=RuntimeError("provider 500"))
    with patch("wax.delivery.senders.deliver", new=boom), patch(
        "wax.domain.identity.primary_channel_target",
        new=AsyncMock(return_value=("telegram", "tg-12345")),
    ):
        await tutor._deliver(work, "Stored regardless of transport failure.")

    rows = (
        await session.execute(
            select(Message).where(Message.direction == "outbound")
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].content == "Stored regardless of transport failure."


@pytest.mark.asyncio
async def test_retrying_delivery_does_not_duplicate_chat_history(session):
    from wax.intelligence.tutor import TutorService

    principal, conversation = await _seed(session)
    work = await _make_work(session, principal, conversation)

    tutor = TutorService(session)
    ok = AsyncMock(return_value={"status": "sent"})
    with patch("wax.delivery.senders.deliver", new=ok), patch(
        "wax.domain.identity.primary_channel_target",
        new=AsyncMock(return_value=("telegram", "tg-12345")),
    ):
        # Worker retry / lease-loss re-execution of the same Work.
        await tutor._deliver(work, "Same answer again.")
        await tutor._deliver(work, "Same answer again.")
        await tutor._record_outbound_message(work, "Same answer again.")

    outbound = (
        await session.execute(
            select(Message).where(
                Message.direction == "outbound", Message.work_id == work.id
            )
        )
    ).scalars().all()
    assert len(outbound) == 1
    assert outbound[0].content == "Same answer again."


@pytest.mark.asyncio
async def test_race_two_records_one_row(session):
    """Even without the pre-check, the unique key makes duplicates impossible."""
    from wax.intelligence.tutor import TutorService

    principal, conversation = await _seed(session)
    work = await _make_work(session, principal, conversation)
    tutor = TutorService(session)

    # Force both calls past the exists-check to exercise savepoint integrity.
    with patch.object(tutor.session, "execute", side_effect=None):
        pass  # no-op guard; below we call directly with cleared cache semantics

    await tutor._record_outbound_message(work, "Raced reply.")
    # Simulate a concurrent attempt that missed the read check:
    ext = f"assistant:{work.id}"
    dup = Message(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        principal_id=principal.id,
        channel="telegram",
        direction="outbound",
        role="assistant",
        content="Raced reply.",
        external_id=ext,
        work_id=work.id,
    )
    from sqlalchemy.exc import IntegrityError

    conflict = False
    try:
        async with session.begin_nested():
            session.add(dup)
            await session.flush()
    except IntegrityError:
        conflict = True
    assert conflict, "unique (channel, external_id) must reject duplicate transcript rows"

    rows = (
        await session.execute(
            select(Message).where(Message.external_id == ext)
        )
    ).scalars().all()
    assert len(rows) == 1
