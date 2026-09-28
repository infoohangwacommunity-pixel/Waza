"""FK / lifecycle source checks that remain valid without deleted modules."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_memory_supersede_creates_before_fk():
    src = (ROOT / "wax/memory/store.py").read_text()
    start = src.find("async def memory_supersede")
    assert start > 0
    body = src[start : start + 2500]
    assert body.find("memory_create") < body.find("superseded_by_id")
    assert "is_active = False" in body
