"""Regression: memory supersede must INSERT new before setting superseded_by_id."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_supersede_flush_order_in_source():
    src = (ROOT / "wax/memory/store.py").read_text()
    start = src.find("async def memory_supersede")
    assert start > 0
    end = src.find("\nasync def ", start + 10)
    if end < 0:
        end = len(src)
    body = src[start:end]
    # New row must be added/flushed before old row FK update
    idx_add = body.find("session.add")
    idx_flush = body.find("flush()", idx_add)
    idx_fk = body.find("superseded_by_id", idx_flush)
    assert idx_add >= 0
    assert idx_flush > idx_add
    assert idx_fk > idx_flush, "FK must be assigned after new row flush"
