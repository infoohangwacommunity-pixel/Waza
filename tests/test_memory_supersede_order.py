"""Regression: memory supersede must create new row before setting superseded_by_id."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_supersede_creates_before_fk():
    src = (ROOT / "wax/memory/store.py").read_text()
    start = src.find("async def memory_supersede")
    assert start > 0
    end = src.find("\nasync def ", start + 10)
    if end < 0:
        end = len(src)
    body = src[start:end]
    idx_create = body.find("memory_create")
    idx_fk = body.find("old.superseded_by_id")
    assert idx_create >= 0
    assert idx_fk > idx_create, "new memory must be created before FK on old row"
