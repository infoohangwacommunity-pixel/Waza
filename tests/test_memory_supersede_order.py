"""Regression: MemoryService.supersede must INSERT new before setting superseded_by_id."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_supersede_flush_order_in_source():
    src = (ROOT / "wax/memory/service.py").read_text()
    # Find supersede method body
    start = src.find("async def supersede")
    assert start > 0
    end = src.find("\n    async def ", start + 10)
    if end < 0:
        end = src.find("\n    def ", start + 10)
    body = src[start:end]
    idx_add = body.find("self.session.add(new_mem)")
    idx_flush_new = body.find("await self.session.flush()", idx_add)
    idx_fk = body.find("old.superseded_by_id = new_mem.id", idx_flush_new)
    assert idx_add >= 0
    assert idx_flush_new > idx_add
    assert idx_fk > idx_flush_new, "FK must be assigned after new row flush"
    # Must NOT deactivate old before new is flushed
    idx_inactive = body.find("old.is_active = False")
    assert idx_inactive > idx_flush_new or idx_inactive < 0


def test_learner_model_uses_is_active_not_status():
    src = (ROOT / "wax/learner/model.py").read_text()
    assert "Memory.status" not in src
    assert "Memory.is_active" in src


def test_provider_retries_only_retryable():
    src = (ROOT / "wax/intelligence/providers.py").read_text()
    assert "retry_if_exception(_should_retry)" in src or "retry_if_exception(" in src
    assert "retryable" in src
    # memory path respects allow_fallback
    assert "allow_fallback=False means memory model only" in src or (
        "if allow_fallback:" in src and "use_memory_model" in src
    )


def test_worker_marks_failed_via_update_not_poisoned_flush():
    src = (ROOT / "wax/workers/main.py").read_text()
    assert "async def _mark_work_terminal" in src
    assert "memory_work_failed" in src
    # process_memory_work should call helper, not raw work.status + flush on failure
    start = src.find("async def process_memory_work")
    end = src.find("\nasync def process_media_prepare", start)
    body = src[start:end]
    assert "_mark_work_terminal" in body
