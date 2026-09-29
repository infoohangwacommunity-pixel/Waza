"""Regression: durable World transcript archive (append-only JSONL).

The DB ``messages`` table stays the operational conversation record; each
student's World gets a long-term archive at ``history/transcript.jsonl`` that
infrastructure writes automatically. These tests prove:

- inbound student messages and assistant replies are archived with complete
  content, timestamps, channel info and message identifiers;
- the archive is idempotent across simulated process restarts (fresh index)
  and delivery retries — no duplicate lines;
- nothing is summarized, truncated, classified or discarded because it is old;
- the complete transcript can be inspected from the World filesystem.
"""

from __future__ import annotations

import json
import uuid

import pytest

from wax.world import layout, manager
from wax.world.transcript import (
    TRANSCRIPT_FILENAME,
    archive_message,
    archive_turn,
    contains_message,
    read_transcript,
    record_message,
    transcript_path,
)


@pytest.fixture
def world_env(tmp_path, monkeypatch):
    """Point the World filesystem at a fresh temp dir per test and clear caches."""
    ws_root = tmp_path / "ws"
    monkeypatch.setenv("WAX_WORKSPACE_ROOT", str(ws_root))
    monkeypatch.setattr(layout, "workspace_root", lambda: ws_root)
    manager._cache.clear()
    import wax.world.transcript as tr

    tr._offset_cache.clear()
    yield tmp_path
    manager._cache.clear()
    tr._offset_cache.clear()


def _make_world(principal_id: str):
    return manager.create_world(principal_id)


def test_transcript_lives_in_durable_history_area(world_env):
    w = _make_world(str(uuid.uuid4()))
    path = transcript_path(w.root)
    assert path == w.root / "history" / TRANSCRIPT_FILENAME
    # history/ is a protected durable area — cleanup never scavenges it.
    from wax.world import cleanup

    assert "history" not in cleanup._TEMP_TOP


def test_record_message_archives_complete_facts(world_env):
    w = _make_world(str(uuid.uuid4))
    mid = uuid.uuid4()
    appended = record_message(
        w.root,
        message_id=mid,
        content="x" * 10_000,  # far beyond any old ~120-char clip
        role="user",
        direction="inbound",
        channel="telegram",
        created_at=None,
        work_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        principal_id=w.principal_id,
        external_id="tg-msg-1",
    )
    assert appended is True
    rows = read_transcript(w.root)
    assert len(rows) == 1
    row = rows[0]
    assert row["content"] == "x" * 10_000  # complete, untransformed
    assert row["message_id"] == str(mid)
    assert row["channel"] == "telegram"
    assert row["direction"] == "inbound"
    assert row["role"] == "user"
    assert row["external_id"] == "tg-msg-1"
    assert row["recorded_at"]  # infrastructure timestamp present


def test_append_only_and_idempotent_within_and_across_restarts(world_env):
    import wax.world.transcript as tr

    w = _make_world(str(uuid.uuid4()))
    mid = uuid.uuid4()
    assert record_message(w.root, message_id=mid, content="hello there",
                          role="user", direction="inbound", channel="whatsapp") is True
    # Same message replayed in-process: no-op.
    assert record_message(w.root, message_id=mid, content="hello there",
                          role="user", direction="inbound", channel="whatsapp") is False
    # Simulated worker restart: drop the in-memory dedup accelerator entirely.
    tr._offset_cache.clear()
    assert contains_message(w.root, mid) is True  # re-scanned from disk
    assert record_message(w.root, message_id=mid, content="hello there",
                          role="user", direction="inbound", channel="whatsapp") is False
    rows = read_transcript(w.root)
    assert len(rows) == 1  # exactly one line despite three attempts


def test_old_messages_are_never_discarded(world_env):
    w = _make_world(str(uuid.uuid4()))
    for i in range(50):
        record_message(w.root, message_id=uuid.uuid4(), content=f"turn {i}",
                       role="user", direction="inbound", channel="telegram")
    # Restart again before reading.
    import wax.world.transcript as tr
    tr._offset_cache.clear()
    rows = read_transcript(w.root)
    assert len(rows) == 50
    assert [r["content"] for r in rows] == [f"turn {i}" for i in range(50)]


def test_torn_final_line_survives_read_without_loss(world_env):
    w = _make_world(str(uuid.uuid4()))
    record_message(w.root, message_id=uuid.uuid4(), content="good line",
                   role="assistant", direction="outbound", channel="telegram")
    # Simulate a crash mid-append: partial last line on disk.
    p = transcript_path(w.root)
    with p.open("a", encoding="utf-8") as fh:
        fh.write('{"message_id": "broken-no-newline')
    rows = read_transcript(w.root)
    assert len(rows) == 1
    assert rows[0]["content"] == "good line"
    # Archive stays append-only: new appends still work after the torn tail.
    assert record_message(w.root, message_id=uuid.uuid4(), content="after crash",
                          role="user", direction="inbound", channel="telegram") is True


# ---------------------------------------------------------------------------
# DB-backed wiring: real Message rows mirrored into the World automatically.
# ---------------------------------------------------------------------------

async def _seed_db(session, principal_id):
    from wax.db.models import Conversation, Message, Principal, Work

    principal = Principal(id=principal_id, display_name="Learner")
    session.add(principal)
    conversation = Conversation(
        id=uuid.uuid4(), principal_id=principal.id, channel="telegram"
    )
    session.add(conversation)
    work = Work(
        id=uuid.uuid4(),
        principal_id=principal.id,
        conversation_id=conversation.id,
        kind="message_response",
        status="queued",
        input_payload={"text": "long question", "channel": "telegram"},
    )
    session.add(work)
    await session.flush()
    inbound = Message(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        principal_id=principal.id,
        channel="telegram",
        direction="inbound",
        role="user",
        content="question " * 500,  # long, must survive intact
        external_id=f"inbound-{work.id}",
        work_id=work.id,
    )
    outbound = Message(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        principal_id=principal.id,
        channel="telegram",
        direction="outbound",
        role="assistant",
        content="answer " * 500,
        external_id=f"assistant:{work.id}",
        work_id=work.id,
    )
    session.add_all([inbound, outbound])
    await session.flush()
    return work, inbound, outbound


@pytest.mark.asyncio
async def test_archive_turn_mirrors_full_conversation(session, world_env):
    pid = uuid.uuid4()
    w = _make_world(str(pid))
    work, inbound, outbound = await _seed_db(session, pid)

    await archive_turn(session, work)
    rows = read_transcript(w.root)
    assert len(rows) == 2
    by_id = {r["message_id"]: r for r in rows}
    assert by_id[str(inbound.id)]["content"] == inbound.content
    assert by_id[str(outbound.id)]["content"] == outbound.content
    assert by_id[str(outbound.id)]["role"] == "assistant"
    assert by_id[str(inbound.id)]["created_at"]  # timestamps preserved
    assert by_id[str(inbound.id)]["channel"] == "telegram"

    # Retry after simulated restart: replay is a pure no-op.
    import wax.world.transcript as tr
    tr._offset_cache.clear()
    await archive_turn(session, work)
    assert len(read_transcript(w.root)) == 2


@pytest.mark.asyncio
async def test_archive_message_after_process_restart(session, world_env):
    pid = uuid.uuid4()
    w = _make_world(str(pid))
    work, inbound, _ = await _seed_db(session, pid)

    await archive_message(session, inbound)
    import wax.world.transcript as tr
    tr._offset_cache.clear()  # fresh process
    await archive_message(session, inbound)  # retry path
    rows = read_transcript(w.root)
    assert len(rows) == 1
    assert json.loads(open(transcript_path(w.root), encoding="utf-8").readline())["content"] == inbound.content


@pytest.mark.asyncio
async def test_no_world_yet_is_safe_noop(session, world_env):
    """A principal without a World has nowhere durable to write; DB remains
    authoritative and archiving simply skips (never raises)."""
    pid = uuid.uuid4()
    _, _, outbound = await _seed_db(session, pid)
    await archive_message(session, outbound)  # must not raise
    assert list((layout.worlds_root() / ".principal_index").glob("*.json")) == []
