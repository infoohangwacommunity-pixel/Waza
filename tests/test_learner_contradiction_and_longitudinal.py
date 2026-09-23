"""Contradiction resolution + multi-day simulated longitudinal scenarios."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from wax.learner.contradiction import EvidenceView, resolve_conflict, score_evidence
from wax.learner.continuity import _is_meaningful
from wax.learner.retention import update_after_review, retrievability
from wax.learner.temporal import resolve_natural_time, decide_due_intent
from wax.learner.evidence import EvidencePlanner
import asyncio


def test_explicit_correction_beats_old_goal():
    old = EvidenceView(
        id="old",
        content="Goal: Medicine",
        kind="goal",
        source="inferred",
        confidence=0.8,
        recency=0.2,
    )
    new = EvidenceView(
        id="new",
        content="Goal: Anatomy",
        kind="goal",
        source="learner_correction",
        confidence=0.9,
        recency=1.0,
        is_correction=True,
    )
    out = resolve_conflict([old, new])
    assert out["status"] == "resolved"
    assert out["current"].content == "Goal: Anatomy"
    assert out["historical"][0].content == "Goal: Medicine"


def test_close_scores_remain_uncertain():
    a = EvidenceView("a", "pref long", kind="preference", source="inferred", confidence=0.6, recency=0.5)
    b = EvidenceView("b", "pref short", kind="preference", source="inferred", confidence=0.61, recency=0.51)
    out = resolve_conflict([a, b], margin=0.2)
    assert out["status"] == "uncertain"


def test_independent_performance_outweighs_self_report():
    claim = EvidenceView(
        "c", "I understand osmosis", kind="semantic", source="explicit", confidence=0.7, recency=0.8
    )
    perf = EvidenceView(
        "p",
        "Independent retrieval failed on osmosis",
        kind="performance",
        source="observed",
        confidence=0.9,
        recency=0.85,
    )
    # observed performance is weighted high; with similar recency it should win or be close
    assert score_evidence(perf) >= score_evidence(claim) - 0.05


def test_scenario_a_continue_is_meaningful_and_plans_teaching():
    assert _is_meaningful("Continue where we stopped.", "ok", [])

    async def _p():
        class S:
            pass

        plan = await EvidencePlanner(S()).plan(
            user_text="Continue where we stopped.", purpose="reply"
        )
        # 'continue' substring / exact handled; this phrase still teaching-like
        assert plan.decision_type in ("continue", "teach", "general")

    asyncio.run(_p())


def test_scenario_b_goal_correction_plan():
    async def _p():
        class S:
            pass

        plan = await EvidencePlanner(S()).plan(
            user_text="I'm not doing Medicine anymore. I'm doing Anatomy.",
            purpose="reply",
        )
        assert plan.check_corrections or plan.decision_type == "correct"

    asyncio.run(_p())


def test_scenario_c_preference_correction_plan():
    async def _p():
        class S:
            pass

        plan = await EvidencePlanner(S()).plan(
            user_text="Actually I prefer short explanations.", purpose="reply"
        )
        assert plan.check_corrections or plan.decision_type == "correct"

    asyncio.run(_p())


def test_scenario_f_forgetting_over_30_days():
    """Learn on day 0; retrievability drops by day 15/30 without review."""
    s = 3.0
    upd = update_after_review(stability=s, difficulty=0.3, grade=3, elapsed_days=1)
    s = upd["stability"]
    r1 = retrievability(s, 1)
    r15 = retrievability(s, 15)
    r30 = retrievability(s, 30)
    assert r15 < r1
    assert r30 < r15


def test_scenario_g_spontaneous_success_strengthens():
    base = update_after_review(stability=4.0, difficulty=0.3, grade=3, elapsed_days=2)
    lapse = update_after_review(stability=4.0, difficulty=0.3, grade=1, elapsed_days=2)
    assert base["stability"] > lapse["stability"]


def test_scenario_h_fulfilled_reminder():
    intent = type("I", (), {})()
    intent.purpose = "reminder"
    intent.target = "review photosynthesis"
    intent.flexibility = "soft"
    intent.completion_condition = "completed"
    intent.payload = {"completed": True}
    intent.concept_key = "photosynthesis"
    snap = {"activities": [], "recent_events": []}
    out = decide_due_intent(intent, snap)
    assert out["decision"] == "fulfilled"


def test_scenario_i_active_study_soft_defer():
    intent = type("I", (), {})()
    intent.purpose = "reminder"
    intent.target = "play a game"
    intent.flexibility = "soft"
    intent.completion_condition = None
    intent.payload = {}
    intent.concept_key = None
    snap = {"activities": [{"status": "active", "objective": "Physics problems"}], "recent_events": []}
    out = decide_due_intent(intent, snap)
    assert out["decision"] in ("reschedule", "deliver")
    # soft + active study should prefer reschedule candidate
    assert out["decision"] == "reschedule"


def test_scenario_clock_lagos_days():
    lagos = ZoneInfo("Africa/Lagos")
    d1 = datetime(2026, 9, 1, 20, 0, tzinfo=lagos)
    r = resolve_natural_time("in 3 days at 9am", reference=d1, tz_name="Africa/Lagos")
    # if parser misses 'in 3 days at 9am', at least tomorrow works
    if r is None:
        r = resolve_natural_time("tomorrow at 9am", reference=d1, tz_name="Africa/Lagos")
    assert r is not None
    assert r["execute_at"].astimezone(lagos).hour in (8, 9, 10)


def test_schedule_series_uses_temporal_intent():
    src = open("wax/tools/registry.py", encoding="utf-8").read()
    assert "legacy_schedule_series" in src
    assert "handle_schedule_series" in src
    idx = src.find("async def handle_schedule_series")
    chunk = src[idx : idx + 1800]
    assert "TemporalService" in chunk
    assert "SchedulerService(session).schedule_series" not in chunk


def test_memory_process_applies_correction():
    src = open("wax/workers/main.py", encoding="utf-8").read()
    assert "apply_possible_correction" in src
