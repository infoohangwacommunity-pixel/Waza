"""Regression: forgetting is AI-selected, never infrastructure-selected.

The old store accepted a free-text `query` to memory_forget and silently
deactivated every row whose content matched the substring — an
application-defined selection policy with destructive consequences (the AI
could wipe many memories with one fuzzy string, and "forget my guitar note"
style phrasing could be misread as a bulk selector).

Now the store refuses any forget that does not name an exact memory_id.
Selection of WHICH memories deserve forgetting belongs to the AI (via its own
search/list/get investigation); infrastructure only deactivates the precise
row it was told to deactivate. Strict principal isolation is preserved.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from wax.db.models import Memory, Principal
from wax.memory import store


async def _principal(session):
    p = Principal(id=uuid.uuid4(), display_name="Learner")
    session.add(p)
    await session.flush()
    return p


async def _mem(session, pid, content):
    m = Memory(
        id=uuid.uuid4(),
        principal_id=pid,
        memory_type="semantic",
        content=content,
        structured={},
        tags=[],
        source="ai",
        is_active=True,
        validity_status="active",
    )
    session.add(m)
    await session.flush()
    return m


@pytest.mark.asyncio
async def test_query_forget_refuses_and_touches_nothing(session):
    """A fuzzy query must never bulk-select rows for destruction."""
    p = await _principal(session)
    ids = []
    for i in range(5):
        m = await _mem(session, p.id, f"guitar practice note {i}")
        ids.append(m.id)

    res = await store.memory_forget(session, p.id, None, query="guitar")

    assert res["ok"] is False
    assert res["error"].startswith("memory_id_required")
    assert res["forgotten_ids"] == []
    # Nothing was deactivated — the destructive selection path is gone.
    rows = list((await session.execute(select(Memory))).scalars().all())
    assert all(r.is_active and r.validity_status == "active" for r in rows)


@pytest.mark.asyncio
async def test_exact_id_forget_works(session):
    p = await _principal(session)
    m1 = await _mem(session, p.id, "likes short worked examples")
    m2 = await _mem(session, p.id, "lives in Nairobi")

    res = await store.memory_forget(session, p.id, str(m1.id))

    assert res["ok"] is True
    assert res["forgotten_ids"] == [str(m1.id)]
    await session.refresh(m1)
    await session.refresh(m2)
    assert m1.is_active is False and m1.validity_status == "forgotten"
    assert m2.is_active is True  # only the named row is affected


@pytest.mark.asyncio
async def test_forget_respects_principal_isolation(session):
    a = await _principal(session)
    b = await _principal(session)
    mb = await _mem(session, b.id, "private fact")

    res = await store.memory_forget(session, a.id, str(mb.id))
    assert res["ok"] is False and res["error"] == "not_found"
    await session.refresh(mb)
    assert mb.is_active is True


@pytest.mark.asyncio
async def test_no_id_no_query_is_clean_error(session):
    p = await _principal(session)
    res = await store.memory_forget(session, p.id, None)
    assert res["ok"] is False
    assert res["error"] == "memory_id_required"
    assert res["forgotten_ids"] == []


def test_store_source_has_no_bulk_forget_selector():
    """Static guard: the forget path contains no substring-matching select
    that could ever again decide FORGETTABLE rows on its own."""
    import inspect

    src = inspect.getsource(store.memory_forget)
    assert "ilike" not in src.lower()
    assert ".limit(30)" not in src
    assert "select(Memory)" not in src  # only session.get by exact id remains


def test_tutor_prompt_teaches_investigate_then_forget_by_id():
    from wax.intelligence.tutor import TUTOR_SYSTEM

    lowered = TUTOR_SYSTEM.lower()
    assert "forget" in lowered
    assert "exact memory_id" in TUTOR_SYSTEM
    assert "will not select rows to forget for you" in lowered
