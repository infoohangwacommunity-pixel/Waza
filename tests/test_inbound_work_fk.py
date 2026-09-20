"""Invariant: inbound_events.work_id must reference an existing works.id.

Regression for ForeignKeyViolationError caused by autoflush=False:
Work was session.add()'d but not flushed before inbound_events.work_id was set,
so the UPDATE ran before the INSERT.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_telegram_flushes_work_before_assigning_inbound_work_id():
    """Handler must flush Work before setting inbound_events.work_id."""
    from wax.messaging.telegram import handler as th

    work_ids_flushed: list = []
    original_flush_points: list = []

    # Capture ordering via a real-ish session mock
    class FakeSession:
        def __init__(self):
            self.added = []
            self._works = {}
            self._events = {}

        def add(self, obj):
            self.added.append(obj)
            from wax.db.models import Work, InboundEvent, Message

            if isinstance(obj, Work):
                self._works[obj.id] = obj
            if isinstance(obj, InboundEvent):
                self._events[obj.id] = obj

        async def flush(self):
            original_flush_points.append("flush")
            # After flush, works are "persisted"
            work_ids_flushed.extend(list(self._works.keys()))

        async def get(self, model, pk):
            from wax.db.models import Work, InboundEvent

            if model is Work:
                return self._works.get(pk)
            if model is InboundEvent:
                return self._events.get(pk)
            return None

        async def execute(self, stmt):
            # First execute is the insert inbound returning id
            eid = uuid.uuid4()
            from wax.db.models import InboundEvent

            ev = InboundEvent(
                id=eid,
                channel="telegram",
                external_event_id="tg-1",
                event_type="message",
                payload={},
                processed=False,
            )
            self._events[eid] = ev

            class R:
                def scalar_one_or_none(self):
                    return eid

            return R()

    session = FakeSession()

    principal = MagicMock()
    principal.id = uuid.uuid4()
    conv = MagicMock()
    conv.id = uuid.uuid4()

    body = b'{"message":{"message_id":1,"from":{"id":99,"first_name":"T"},"chat":{"id":99},"text":"hi"}}'

    with (
        patch.object(th.settings, "telegram_webhook_secret", ""),
        patch.object(th, "session_scope") as scope,
        patch.object(th, "_resolve_identity", new=AsyncMock(return_value=(principal, MagicMock()))),
        patch.object(th, "_get_or_create_conversation", new=AsyncMock(return_value=conv)),
        patch.object(
            th,
            "normalize_telegram_update",
            return_value=MagicMock(
                external_event_id="tg-1",
                external_user_id="99",
                text="hi",
                content_type="text",
                media_id=None,
            ),
        ),
    ):
        # session_scope as async context manager yielding our fake session
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=session)
        cm.__aexit__ = AsyncMock(return_value=False)
        scope.return_value = cm

        result = await th.handle_telegram_webhook(body, {})

    assert result["status"] == "ok"
    assert "flush" in original_flush_points
    # Work must have been flushed (present in work_ids_flushed) before link
    assert work_ids_flushed, "Work was never flushed before linking inbound event"
    # inbound event must point at a flushed work
    events = list(session._events.values())
    assert events
    assert events[0].work_id in work_ids_flushed
    assert events[0].processed is True


@pytest.mark.asyncio
async def test_whatsapp_handler_contains_flush_before_work_id():
    """Source-level guard: WhatsApp path must flush after add(work)."""
    from pathlib import Path

    src = Path("wax/messaging/whatsapp/handler.py").read_text()
    # crude but effective regression lock
    idx_add = src.find("session.add(work)")
    idx_flush = src.find("await session.flush()", idx_add)
    idx_assign = src.find("event.work_id = work.id", idx_add)
    assert idx_add != -1 and idx_flush != -1 and idx_assign != -1
    assert idx_flush < idx_assign, "flush must precede work_id assignment"
