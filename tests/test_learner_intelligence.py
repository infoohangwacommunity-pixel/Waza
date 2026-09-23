"""Learner intelligence — temporal resolution, retention math."""

from datetime import datetime
from zoneinfo import ZoneInfo

from wax.learner.temporal import resolve_natural_time
from wax.learner.retention import update_after_review, days_until_retrievability, retrievability


def test_tomorrow_at_10_lagos():
    ref = datetime(2026, 9, 23, 20, 35, tzinfo=ZoneInfo("Africa/Lagos"))
    r = resolve_natural_time("Remind me tomorrow at 10am", reference=ref, tz_name="Africa/Lagos")
    assert r is not None
    local = datetime.fromisoformat(r["local"])
    assert local.year == 2026 and local.month == 9 and local.day == 24
    assert local.hour == 10


def test_in_2_hours():
    ref = datetime(2026, 9, 23, 8, 0, tzinfo=ZoneInfo("UTC"))
    r = resolve_natural_time("in 2 hours", reference=ref, tz_name="UTC")
    assert r is not None
    assert r["execute_at"].hour == 10


def test_retention_success_increases_stability():
    upd = update_after_review(stability=2.0, difficulty=0.3, grade=3, elapsed_days=1.5)
    assert upd["stability"] > 2.0
    assert upd["lapse"] == 0.0


def test_retention_lapse_decreases_stability():
    upd = update_after_review(stability=10.0, difficulty=0.3, grade=1, elapsed_days=5.0)
    assert upd["stability"] < 10.0
    assert upd["lapse"] == 1.0


def test_retrievability_decays():
    assert abs(retrievability(5.0, 0.0) - 1.0) < 1e-9
    assert retrievability(5.0, 5.0) < 1.0
    assert retrievability(5.0, 50.0) < retrievability(5.0, 5.0)


def test_days_until_positive():
    assert days_until_retrievability(5.0, 0.9) > 0
