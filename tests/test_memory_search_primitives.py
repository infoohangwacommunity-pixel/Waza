"""Regression: memory retrieval is an explicit mechanical primitive, not intelligence.

The store applies plain textual filters chosen by the AI (literal terms with
any/all composition, field selection, tag containment, type filter, recency
ordering, pagination). It NEVER decides which memories are relevant: no
embeddings, no similarity scores, no keyword routing, no ranking policy, no
automatic "top memories" injection. The AI formulates queries; infrastructure
answers mechanically. Strict principal isolation is preserved.
"""

from __future__ import annotations

import re
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


async def _mem(session, pid, content, *, mtype="semantic", tags=None, structured=None,
               active=True):
    m = Memory(
        id=uuid.uuid4(),
        principal_id=pid,
        memory_type=mtype,
        content=content,
        tags=tags or [],
        structured=structured or {},
        is_active=active,
        validity_status="active" if active else "forgotten",
    )
    session.add(m)
    await session.flush()
    return m


@pytest.mark.asyncio
async def test_terms_match_across_content_tags_structured(session):
    p = await _principal(session)
    in_content = await _mem(session, p.id, "student likes short worked examples")
    in_tags = await _mem(session, p.id, "unrelated note", tags=["algebra"])
    in_struct = await _mem(session, p.id, "another note", structured={"topic": "algebra"})
    other = await _mem(session, p.id, "nothing to do with it")

    res = await store.memory_search(session, p.id, terms=["algebra"],
                                    fields=["content", "tags", "structured"])
    assert res["ok"]
    ids = {m["id"] for m in res["memories"]}
    assert ids == {str(in_tags.id), str(in_struct.id)}
    assert other.id not in [uuid.UUID(i) for i in ids]
    # Echo of the AI's own query so the model can see what was actually asked.
    assert res["query_terms"] == ["algebra"]
    assert set(res["fields"]) == {"content", "tags", "structured"}

    # Default fields stay broad; content-only search excludes tag/struct hits.
    res2 = await store.memory_search(session, p.id, terms=["algebra"], fields=["content"])
    assert res2["count"] == 0


@pytest.mark.asyncio
async def test_mode_all_vs_any_is_boolean_composition_not_ranking(session):
    p = await _principal(session)
    both = await _mem(session, p.id, "prefers short examples over lectures")
    one = await _mem(session, p.id, "long lecture transcripts reviewed")

    any_res = await store.memory_search(session, p.id, terms=["short", "lecture"], mode="any")
    assert {m["id"] for m in any_res["memories"]} == {str(both.id), str(one.id)}

    all_res = await store.memory_search(session, p.id, terms=["short", "lecture"], mode="all")
    assert {m["id"] for m in all_res["memories"]} == {str(both.id)}
    # Recency ordering only — no score field may ever appear.
    for r in (any_res, all_res):
        for m in r["memories"]:
            assert "score" not in m and "similarity" not in m and "relevance" not in m


@pytest.mark.asyncio
async def test_quoted_phrase_is_exact_substring_mechanically(session):
    p = await _principal(session)
    exact = await _mem(session, p.id, 'uses "spaced repetition" schedule')
    loose = await _mem(session, p.id, "talks about spacing and repetition separately")

    res = await store.memory_search(session, p.id, terms=['"spaced repetition"'])
    assert [m["id"] for m in res["memories"]] == [str(exact.id)]
    res_any = await store.memory_search(session, p.id, terms=["spaced", "repetition"], mode="any")
    assert len(res_any["memories"]) == 2  # words match loosely; phrase does not


@pytest.mark.asyncio
async def test_pagination_returns_everything_no_top_selection(session):
    p = await _principal(session)
    created = [await _mem(session, p.id, f"calculus drill number {i}") for i in range(7)]

    page1 = await store.memory_search(session, p.id, terms=["calculus", "drill"], limit=3)
    assert page1["count"] == 3 and page1["next_offset"] == 3
    page2 = await store.memory_search(session, p.id, terms=["calculus", "drill"],
                                      limit=3, offset=3)
    page3 = await store.memory_search(session, p.id, terms=["calculus", "drill"],
                                      limit=3, offset=6)
    seen = [m["id"] for pg in (page1, page2, page3) for m in pg["memories"]]
    assert len(set(seen)) == 7  # paging sees ALL matches; nothing pre-selected away
    assert "next_offset" not in page3

    listed = await store.memory_list(session, p.id, limit=50)
    assert {m["id"] for m in listed["memories"]} == {str(m.id) for m in created}


@pytest.mark.asyncio
async def test_tag_and_type_filters_are_exact_containment(session):
    p = await _principal(session)
    hit = await _mem(session, p.id, "reviewed derivatives", mtype="episodic",
                     tags=["teaching", "calculus"])
    wrong_tag = await _mem(session, p.id, "derivatives again", mtype="episodic",
                           tags=["teaching"])
    wrong_type = await _mem(session, p.id, "derivatives once more", mtype="semantic",
                            tags=["teaching", "calculus"])

    res = await store.memory_search(session, p.id, tags=["teaching", "calculus"],
                                    memory_type="episodic")
    assert [m["id"] for m in res["memories"]] == [str(hit.id)]
    assert str(wrong_tag.id) not in [m["id"] for m in res["memories"]]
    assert str(wrong_type.id) not in [m["id"] for m in res["memories"]]


@pytest.mark.asyncio
async def test_strict_principal_isolation_across_primitives(session):
    a = await _principal(session)
    b = await _principal(session)
    shared_word = "photosynthesis revision plan"
    await _mem(session, a.id, shared_word)
    await _mem(session, b.id, shared_word, tags=["biology"])

    ra = await store.memory_search(session, a.id, terms=["photosynthesis"],
                                   fields=["content", "tags", "structured"])
    rb = await store.memory_search(session, b.id, terms=["photosynthesis"],
                                   fields=["content", "tags", "structured"])
    assert ra["count"] == 1 and rb["count"] == 1
    # Same word, different owners: each result belongs only to its principal.
    assert all(m["id"] for m in ra["memories"])
    la = await store.memory_list(session, a.id)
    lb = await store.memory_list(session, b.id)
    assert {m["id"] for m in la["memories"]} != {m["id"] for m in lb["memories"]}
    # Forgotten rows hidden unless explicitly requested by the AI.
    forgotten = await _mem(session, a.id, "old gym routine", active=False)
    hid = await store.memory_search(session, a.id, terms=["gym"])
    assert hid["count"] == 0
    shown = await store.memory_search(session, a.id, terms=["gym"], include_inactive=True)
    assert [m["id"] for m in shown["memories"]] == [str(forgotten.id)]


@pytest.mark.asyncio
async def test_term_cap_is_mechanical_transport_bound_not_selection(session):
    p = await _principal(session)
    await _mem(session, p.id, "alpha omega bridge")
    terms = [f"term{i}" for i in range(20)] + ["omega"]
    res = await store.memory_search(session, p.id, terms=terms, mode="any")
    assert res["ok"]
    assert len(res["query_terms"]) == store.MAX_TERMS
    assert res["query_terms"][0] == "term0"  # first terms kept, order preserved
    assert "dropped_terms" in res and "omega" in res["dropped_terms"]
    # The AI still gets its honest answer for the terms that were applied;
    # dropped terms are reported, never silently used to pick "better" ones.


def test_store_source_contains_no_intelligence_layer():
    src = open("wax/memory/store.py", encoding="utf-8").read()
    # Strip string literals (prose) — scan executable code only.
    code = re.sub(r'""".*?"""', "", src, flags=re.S)
    code = re.sub(r"'(?:\\.|[^'\\])*'", "", code)
    code = re.sub(r'"(?:\\.|[^"\\])*"', "", code)
    lowered = code.lower()
    for banned in ("embedding", "vector", "cosine", "similarity", "tsrank",
                   "bm25", "trigram", "pg_trgm", "to_tsvector", "semantic_rank"):
        assert banned not in lowered, f"intelligence machinery in store: {banned}"
    assert "updated_at.desc()" in src  # recency only, never relevance ordering


def test_no_automatic_memory_selection_or_injection_anywhere():
    """The application must never choose memories for the AI."""
    tutor = open("wax/intelligence/tutor.py", encoding="utf-8").read()
    worker = open("wax/workers/main.py", encoding="utf-8").read()
    # memory_search is reachable ONLY from TutorService._state (the AI's
    # explicit `state` directive handler). No other call site exists.
    import subprocess

    grep = subprocess.run(
        ["grep", "-rn", "memory_search\\|memory_list", "wax/", "--include=*.py"],
        capture_output=True, text=True,
    ).stdout
    callers = {
        line.split(":")[0]
        for line in grep.splitlines()
        if "def memory_" not in line and "wax/memory/store.py" not in line
    }
    assert callers == {"wax/intelligence/tutor.py"}, callers
    # In the tutor, the only caller is the directive executor _state().
    state_idx = tutor.index("async def _state")
    before_state = tutor[:state_idx]
    assert "memory_search(" not in before_state.replace("action: search", "")
    assert "memory_list(" not in before_state
    # No post-turn / automatic memory job in the worker.
    assert "memory_search" not in worker and "memory_list" not in worker
    assert "_memory_snapshot" not in tutor
    # Results are returned verbatim to the model — no top-k re-cut after the store.
    assert "memories\"][: " not in tutor and "sorted(memor" not in tutor
