"""Longitudinal / scenario unit tests for Learner Intelligence primitives."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from wax.learner.temporal import resolve_natural_time
from wax.learner.retention import update_after_review, retrievability, days_until_retrievability
from wax.learner.evidence import EvidencePlanner
import asyncio


def test_multi_day_time_resolution_chain():
    """Day1 reminder language → absolute local time; day2 different ref."""
    lagos = ZoneInfo("Africa/Lagos")
    day1 = datetime(2026, 9, 23, 20, 35, tzinfo=lagos)
    r1 = resolve_natural_time("tomorrow at 10am", reference=day1, tz_name="Africa/Lagos")
    assert r1["execute_at"].astimezone(lagos).day == 24
    day2 = datetime(2026, 9, 24, 11, 0, tzinfo=lagos)
    r2 = resolve_natural_time("in 2 hours", reference=day2, tz_name="Africa/Lagos")
    assert r2["execute_at"].astimezone(lagos).hour == 13


def test_forgetting_curve_over_days():
    """Stability growth then decay of retrievability over elapsed days."""
    s = 2.0
    d = 0.3
    for _ in range(3):
        upd = update_after_review(stability=s, difficulty=d, grade=3, elapsed_days=s * 0.9)
        s, d = upd["stability"], upd["difficulty"]
    assert s > 2.0
    r_fresh = retrievability(s, 0.1)
    r_stale = retrievability(s, s * 3)
    assert r_stale < r_fresh
    assert days_until_retrievability(s, 0.9) > 0


def test_lapse_then_relearn_differs_from_fresh():
    strong = update_after_review(stability=20.0, difficulty=0.2, grade=3, elapsed_days=10)
    lapse = update_after_review(stability=20.0, difficulty=0.2, grade=1, elapsed_days=10)
    assert lapse["stability"] < strong["stability"]
    relearn = update_after_review(
        stability=lapse["stability"], difficulty=lapse["difficulty"], grade=3, elapsed_days=1
    )
    assert relearn["stability"] > lapse["stability"]


def test_evidence_planner_continue_deterministic():
    async def _run():
        class DummySession:
            pass
        plan = await EvidencePlanner(DummySession()).plan(user_text="continue", purpose="reply")
        assert plan.decision_type == "continue"
        assert "teaching_state" in plan.need

    asyncio.run(_run())


def test_evidence_planner_reminder_deterministic():
    async def _run():
        class DummySession:
            pass
        plan = await EvidencePlanner(DummySession()).plan(
            user_text="Remind me tomorrow at 10 to play GTA", purpose="reply"
        )
        assert plan.decision_type == "schedule"
        assert "temporal" in plan.need

    asyncio.run(_run())


def test_evidence_planner_correction_signal():
    async def _run():
        class DummySession:
            pass
        plan = await EvidencePlanner(DummySession()).plan(
            user_text="I'm not doing Medicine anymore. I'm doing Anatomy.",
            purpose="reply",
        )
        assert plan.decision_type == "correct"
        assert plan.check_corrections

    asyncio.run(_run())
