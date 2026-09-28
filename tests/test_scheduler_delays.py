"""Time infrastructure: delay parsing and series bounds (no DB)."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from wax.scheduler.ops import _resolve_execute_at


def test_delay_3_seconds():
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    at, err = _resolve_execute_at({"delay_seconds": 3}, now)
    assert err is None
    assert at is not None
    assert abs((at - now).total_seconds() - 3) < 0.01


def test_delay_5_seconds():
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    at, err = _resolve_execute_at({"delay_seconds": 5}, now)
    assert err is None and at is not None
    assert abs((at - now).total_seconds() - 5) < 0.01


def test_delay_minutes():
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    at, err = _resolve_execute_at({"delay_minutes": 15}, now)
    assert err is None and at is not None
    assert abs((at - now).total_seconds() - 900) < 0.01


def test_execute_at_iso():
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    at, err = _resolve_execute_at({"execute_at": "2026-09-29T08:00:00Z"}, now)
    assert err is None and at is not None
    assert at.year == 2026 and at.month == 9 and at.day == 29


def test_minimum_floor_half_second():
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    at, err = _resolve_execute_at({"delay_seconds": 0}, now)
    assert err is None and at is not None
    assert (at - now).total_seconds() >= 0.5


def test_due_actions_uses_skip_locked():
    src = open("wax/scheduler/service.py", encoding="utf-8").read()
    assert "with_for_update(skip_locked=True)" in src
    assert "recover_stuck_executing" in src
    assert "create_work_for_action" in src


def test_no_reminder_hardcode_in_scheduler():
    for path in ("wax/scheduler/ops.py", "wax/scheduler/service.py"):
        src = open(path, encoding="utf-8").read().lower()
        assert "if reason == \"reminder\"" not in src
        assert "keyword" not in src or "not a" in open(path, encoding="utf-8").read().lower()
