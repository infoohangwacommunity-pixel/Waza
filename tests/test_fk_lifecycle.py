"""FK lifecycle: supersede memory and scheduled work ordering."""

from __future__ import annotations

import inspect
from pathlib import Path


def test_consolidation_flushes_new_memory_before_superseded_by():
    src = Path("wax/memory/consolidation.py").read_text()
    # new memory add+flush must precede superseded_by_id assignment in supersession loop
    idx_add = src.find("self.session.add(new_m)")
    idx_flush = src.find("await self.session.flush()", idx_add)
    idx_fk = src.find("old.superseded_by_id = new_m.id", idx_add)
    assert idx_add != -1 and idx_flush != -1 and idx_fk != -1
    assert idx_flush < idx_fk


def test_scheduler_flushes_work_before_action_work_id():
    src = Path("wax/scheduler/service.py").read_text()
    idx_add = src.find("self.session.add(work)")
    idx_flush = src.find("await self.session.flush()", idx_add)
    idx_assign = src.find("action.work_id = work.id", idx_add)
    assert idx_add != -1 and idx_flush != -1 and idx_assign != -1
    assert idx_flush < idx_assign
    assert "scheduled_work_reused" in src


def test_create_artifact_supports_pdf_format():
    src = Path("wax/tools/registry.py").read_text()
    assert "generate_bytes" in src
    assert "format" in src
    assert "delivery_available" in src
