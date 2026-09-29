"""Memory store: lifecycle ops and principal isolation (unit-level)."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from wax.memory.store import _as_uuid, _owned, _row_to_dict


def test_as_uuid_roundtrip():
    u = uuid4()
    assert _as_uuid(u) == u
    assert _as_uuid(str(u)) == u
    assert _as_uuid("not-a-uuid") is None
    assert _as_uuid(None) is None


def test_owned_rejects_other_principal():
    a, b = uuid4(), uuid4()
    row = SimpleNamespace(principal_id=a)
    assert _owned(row, a) is True
    assert _owned(row, b) is False
    assert _owned(None, a) is False


def test_row_to_dict_shape():
    u = uuid4()
    m = SimpleNamespace(
        id=u,
        memory_type="semantic",
        content="likes short examples",
        tags=["pref"],
        structured={},
        is_active=True,
        validity_status="active",
        superseded_by_id=None,
        created_at=None,
        updated_at=None,
    )
    d = _row_to_dict(m)
    assert d["id"] == str(u)
    assert d["content"] == "likes short examples"
    assert d["is_active"] is True


def test_store_exports_full_lifecycle():
    from wax.memory import store

    for name in (
        "memory_search",
        "memory_get",
        "memory_create",
        "memory_update",
        "memory_supersede",
        "memory_forget",
    ):
        assert callable(getattr(store, name))


def test_no_memory_service_shim():
    import importlib.util
    from pathlib import Path

    service = Path("wax/memory/service.py")
    assert not service.exists(), "MemoryService compatibility shim must stay deleted"
    assert importlib.util.find_spec("wax.memory.service") is None


def test_no_plan_and_retrieve_or_auto_summary_in_store():
    src = open("wax/memory/store.py", encoding="utf-8").read()
    assert "plan_and_retrieve" not in src
    assert "get_active_summary" not in src
    assert "def extract_and_store" not in src
    assert "def consolidate" not in src
    assert "importance.desc" not in src
    assert "order_by(Memory.updated_at.desc())" in src
    models = open("wax/db/models.py", encoding="utf-8").read()
    mem = models.split("class Memory")[1].split("class Artifact")[0]
    assert "contradiction_of" not in mem
    assert "confidence" not in mem


def test_tutor_does_not_auto_inject_memories():
    src = open("wax/intelligence/tutor.py", encoding="utf-8").read()
    assert "_memory_snapshot" not in src
    assert "memory_search(self.session, principal_id, query=None, limit=12)" not in src
    assert "Nothing is preloaded" in src or "preloaded" in src


def test_no_post_turn_memory_process_job():
    src = open("wax/workers/main.py", encoding="utf-8").read()
    assert "memory_process" not in src
    assert "process_memory_work" not in src
