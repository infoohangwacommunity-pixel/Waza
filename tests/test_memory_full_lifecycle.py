"""Regression: the complete AI-owned memory lifecycle through the state directive.

The AI can explicitly create, inspect (get/list/search), update, supersede and
forget memories; attach its own tags/structured/metadata when it chooses; and
reference the originating message/work when available. Infrastructure stores,
isolates and reports clear results — it never auto-generates tags, infers
importance, consolidates or summarizes.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from wax.db.models import Memory, Principal, Work
from wax.intelligence.directives import Directive
from wax.intelligence.tutor import TutorService
from wax.memory import store


async def _principal(session) -> Principal:
    p = Principal(id=uuid.uuid4(), display_name="Learner")
    session.add(p)
    await session.flush()
    return p


async def _work(session, pid) -> Work:
    w = Work(
        id=uuid.uuid4(),
        principal_id=pid,
        kind="message_response",
        status="running",
        input_payload={"channel": "whatsapp"},
    )
    session.add(w)
    await session.flush()
    return w


def _state(action: str, **fields) -> Directive:
    return Directive(channel="state", body="", fields={"action": action, **fields})


@pytest.fixture
def tutor(session):
    return TutorService(session=session)


# ---------------------------------------------------------------- lifecycle


@pytest.mark.asyncio
async def test_create_with_ai_chosen_metadata_and_refs(tutor, session):
    p = await _principal(session)
    w = await _work(session, p.id)
    msg_id = str(uuid.uuid4())

    res = await tutor._state(
        _state(
            "create",
            content="Prefers worked examples over long theory",
            memory_type="preference",
            tags=["style", "math"],
            structured={"confidence_note": "stated twice"},
            metadata={"origin": "chat on 2026-09-29"},
            expires_at="2027-01-01T00:00:00Z",
            message_id=msg_id,
        ),
        p.id,
        work=w,
    )
    assert res["ok"]
    m = res["memory"]
    # Everything the AI attached is stored exactly as given — nothing invented.
    assert m["tags"] == ["style", "math"]
    assert m["structured"]["confidence_note"] == "stated twice"
    assert m["metadata"]["origin"] == "chat on 2026-09-29"
    assert m["expires_at"].startswith("2027-01-01")
    # Originating references: explicit message_id + current work recorded.
    assert m["source_message_id"] == msg_id
    assert m["source_work_id"] == str(w.id)


@pytest.mark.asyncio
async def test_create_without_optional_fields_stays_empty(tutor, session):
    """No automatic tag generation, no inferred importance, no injected metadata."""
    p = await _principal(session)
    res = await tutor._state(
        _state("create", content="Student name is Ada"), p.id
    )
    assert res["ok"]
    m = res["memory"]
    assert m["tags"] == []
    assert m["structured"] == {}
    assert m["metadata"] == {}
    assert m["expires_at"] is None
    assert m["source_work_id"] is None  # no work supplied → none fabricated


@pytest.mark.asyncio
async def test_inspect_get_list_search(tutor, session):
    p = await _principal(session)
    created = await tutor._state(
        _state("create", content="Loves astronomy", tags=["topics"]), p.id
    )
    mid = created["memory"]["id"]

    got = await tutor._state(_state("get", memory_id=mid), p.id)
    assert got["ok"] and got["memory"]["content"] == "Loves astronomy"

    inspected = await tutor._state(_state("inspect", memory_id=mid), p.id)
    assert inspected["ok"]  # alias behaves identically

    listed = await tutor._state(_state("list"), p.id)
    assert listed["ok"] and [m["id"] for m in listed["memories"]] == [mid]

    searched = await tutor._state(_state("search", terms=["astronomy"]), p.id)
    assert searched["ok"] and searched["count"] == 1


@pytest.mark.asyncio
async def test_update_changes_only_named_fields(tutor, session):
    p = await _principal(session)
    created = await tutor._state(
        _state(
            "create",
            content="Working on fractions",
            tags=["current"],
            metadata={"week": 1},
        ),
        p.id,
    )
    mid = created["memory"]["id"]

    upd = await tutor._state(
        _state("update", memory_id=mid, content="Finished fractions, started algebra"),
        p.id,
    )
    assert upd["ok"]
    # Absent fields are untouched — plain storage semantics, not consolidation.
    assert upd["memory"]["content"].endswith("started algebra")
    assert upd["memory"]["tags"] == ["current"]
    assert upd["memory"]["metadata"] == {"week": 1}

    # AI may replace its own tags/metadata explicitly.
    upd2 = await tutor._state(
        _state(
            "update",
            memory_id=mid,
            tags=["current", "algebra"],
            metadata={"week": 2},
        ),
        p.id,
    )
    assert upd2["memory"]["tags"] == ["current", "algebra"]
    assert upd2["memory"]["metadata"] == {"week": 2}


@pytest.mark.asyncio
async def test_supersede_keeps_old_row_as_history(tutor, session):
    p = await _principal(session)
    w = await _work(session, p.id)
    old = await tutor._state(
        _state("create", content="Lives in Nairobi", tags=["location"]), p.id
    )
    old_id = old["memory"]["id"]

    sup = await tutor._state(
        _state(
            "supersede",
            memory_id=old_id,
            content="Moved to Mombasa",
            reason="student announced the move themselves",
        ),
        p.id,
        work=w,
    )
    assert sup["ok"]
    new_id = sup["new_memory"]["id"]

    # Old row preserved, deactivated, linked forward — never deleted silently.
    old_row = await session.get(Memory, uuid.UUID(old_id))
    assert old_row.is_active is False
    assert old_row.validity_status == "superseded"
    assert str(old_row.superseded_by_id) == new_id

    # New row carries provenance the AI can later inspect.
    assert sup["new_memory"]["structured"]["supersedes"] == old_id
    assert "moved the move" in sup["new_memory"]["structured"]["supersede_reason"] \
        or sup["new_memory"]["structured"]["supersede_reason"] != ""
    # AI's own tags carried over unless replaced (plain copy, no inference).
    assert sup["new_memory"]["tags"] == ["location"]
    # Originating work referenced automatically when available (storage fact).
    assert sup["new_memory"]["source_work_id"] == str(w.id)

    # Superseded row hidden from default search/list, visible when asked.
    s1 = await tutor._state(_state("search", terms=["Nairobi"]), p.id)
    assert s1["count"] == 0
    s2 = await tutor._state(
        _state("search", terms=["Nairobi"], include_inactive=True), p.id
    )
    assert s2["count"] == 1


@pytest.mark.asyncio
async def test_forget_requires_exact_id_and_reports_clear_result(tutor, session):
    p = await _principal(session)
    created = await tutor._state(
        _state("create", content="Temporary note about a trip"), p.id
    )
    mid = created["memory"]["id"]

    res = await tutor._state(_state("forget", memory_id=mid), p.id)
    assert res["ok"] and res["forgotten_ids"] == [mid] and res["count"] == 1

    row = await session.get(Memory, uuid.UUID(mid))
    assert row.is_active is False
    assert row.validity_status == "forgotten"  # durable record remains

    # delete alias behaves identically
    c2 = await tutor._state(_state("create", content="Another note"), p.id)
    d = await tutor._state(_state("delete", memory_id=c2["memory"]["id"]), p.id)
    assert d["ok"] and d["forgotten_ids"] == [c2["memory"]["id"]]

    # Forgetting without an exact id refuses and touches nothing.
    bad = await tutor._state(_state("forget", query="note"), p.id)
    assert not bad["ok"]
    assert bad["error"].startswith("memory_id_required")


@pytest.mark.asyncio
async def test_isolation_across_principals_full_lifecycle(tutor, session):
    alice = await _principal(session)
    bob = await _principal(session)
    a = await tutor._state(_state("create", content="Alice private fact"), alice.id)
    aid = a["memory"]["id"]

    for action, extra in [
        ("get", {"memory_id": aid}),
        ("update", {"memory_id": aid, "content": "hijacked"}),
        ("supersede", {"memory_id": aid, "new_content": "x"}),
        ("forget", {"memory_id": aid}),
    ]:
        res = await tutor._state(_state(action, **extra), bob.id)
        assert not res["ok"], f"bob must not {action} alice's memory"

    still = await tutor._state(_state("get", memory_id=aid), alice.id)
    assert still["ok"] and still["memory"]["content"] == "Alice private fact"

    lst = await tutor._state(_state("list"), bob.id)
    assert lst["ok"] and lst["count"] == 0


# ------------------------------------------------- infrastructure honesty


@pytest.mark.asyncio
async def test_no_automatic_consolidation_or_summarization(session):
    """Many similar memories stay many separate rows until the AI acts."""
    p = await _principal(session)
    ids = []
    for i in range(5):
        r = await store.memory_create(
            session, p.id, content=f"Practice log entry number {i}"
        )
        ids.append(r["memory"]["id"])
    await session.commit()

    rows = list(
        (
            await session.execute(
                select(Memory).where(Memory.principal_id == p.id)
            )
        ).scalars().all()
    )
    assert len(rows) == 5  # nothing merged, dropped, or summarized away
    assert all(str(r.id) in ids for r in rows)


def test_store_exports_remain_pure_primitives():
    # Scan executable code only — prose that *forbids* intelligence (docstrings,
    # comments) must not trip the guard. Strip docstrings/comments via AST/tokenize.
    import ast as _ast
    import io
    import tokenize

    path = "wax/memory/store.py"
    tree = _ast.parse(open(path).read())
    doc_nodes = set()
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.Module, _ast.ClassDef, _ast.FunctionDef, _ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], _ast.Expr) and isinstance(body[0].value, _ast.Constant) \
                    and isinstance(body[0].value.value, str):
                doc_nodes.add(id(body[0]))
    lines_without_docs = []
    for node in _ast.walk(tree):
        if id(node) in doc_nodes:
            continue
        if isinstance(node, (_ast.Assign,)) and any(
            isinstance(t, _ast.Name) and t.id == "__doc__" for t in node.targets
        ):
            continue
    # Simpler robust approach: strip string tokens & comment tokens from source.
    out_lines = []
    with io.StringIO(open(path).read()) as buf:
        depth = 0
        for tok in tokenize.generate_tokens(buf.readline):
            if tok.type == tokenize.COMMENT or (tok.type == tokenize.STRING and depth <= 1 and "\n" in tok.string):
                continue
            if tok.type == tokenize.OP and tok.string in "([{":
                depth += 1
            elif tok.type == tokenize.OP and tok.string in ")]}":
                depth -= 1
            out_lines.append(tok.string if tok.type != tokenize.NEWLINE else "\n")
    code_only = "".join(out_lines)
    for banned in (
        "auto_tag", "infer_importance", "consolidate(", "summarize(",
        "embedding", "similarity", "relevance", "rank(",
    ):
        assert banned not in code_only, f"intelligence leaked into store code: {banned}"


def test_tutor_never_writes_state_on_its_own():
    """State writes happen only inside _state (directive-driven)."""
    import inspect as pyinspect

    src = pyinspect.getsource(TutorService)
    # memory_create/update/supersede/forget appear ONLY within _state scope.
    state_src = pyinspect.getsource(TutorService._state)
    outside = src.replace(state_src, "")
    for fn in ("memory_create", "memory_update", "memory_supersede", "memory_forget"):
        assert fn not in outside, f"{fn} called outside the state directive"
