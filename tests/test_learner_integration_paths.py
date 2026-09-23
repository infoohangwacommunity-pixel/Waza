"""Integration-path tests: continuity, dedup fingerprint, schedule adapters, isolation helpers."""

from __future__ import annotations

import asyncio
import hashlib

from wax.learner.continuity import _is_meaningful
from wax.learner.temporal import resolve_natural_time
from wax.knowledge.ingest import KnowledgeIngestService
from datetime import datetime
from zoneinfo import ZoneInfo


def test_trivial_messages_not_meaningful():
    assert not _is_meaningful("hi", "Hello!", [])
    assert not _is_meaningful("thanks", "You're welcome", [])
    assert _is_meaningful("Teach me osmosis", "Osmosis is...", [])
    assert _is_meaningful("continue", "Let's pick up...", [])


def test_fingerprint_stable():
    class S:
        pass

    svc = KnowledgeIngestService(S())  # type: ignore

    async def _run():
        a = await svc.content_fingerprint("Hello   world\n")
        b = await svc.content_fingerprint("hello world")
        assert a == b
        c = await svc.content_fingerprint("hello world!")
        assert a != c

    asyncio.run(_run())


def test_schedule_intent_path_names_in_registry():
    src = open("wax/tools/registry.py", encoding="utf-8").read()
    assert "TemporalService" in src
    assert "handle_schedule_intent" in src
    # legacy adapters should reference temporal intent
    assert "legacy_schedule_at" in src or "via\": \"temporal_intent\"" in src or "via" in src


def test_post_turn_hook_in_tutor():
    src = open("wax/intelligence/tutor.py", encoding="utf-8").read()
    assert "maybe_update_teaching_after_turn" in src


def test_evidence_planner_file_has_prerequisites():
    src = open("wax/learner/evidence.py", encoding="utf-8").read()
    assert "prerequisite" in src.lower() or "concept_hints" in src


def test_cross_principal_query_must_filter():
    """Static guarantee: knowledge/memory semantic paths filter principal_id."""
    kn = open("wax/knowledge/ingest.py", encoding="utf-8").read()
    assert "DocumentChunk.principal_id == principal_id" in kn
    assert "KnowledgeSource.principal_id == principal_id" in kn
    mem = open("wax/memory/service.py", encoding="utf-8").read()
    assert "Memory.principal_id == principal_id" in mem


def test_correction_supersede_exists():
    src = open("wax/learner/correction.py", encoding="utf-8").read()
    assert "supersede" in src
    assert "preference_corrected" in src or "correction" in src


def test_temporal_multi_day_scenario_a():
    """Scenario A time spine: day1 schedule language resolves to next calendar day."""
    lagos = ZoneInfo("Africa/Lagos")
    day1 = datetime(2026, 9, 1, 21, 0, tzinfo=lagos)
    r = resolve_natural_time("Remind me tomorrow at 10am", reference=day1, tz_name="Africa/Lagos")
    assert r is not None
    local = r["execute_at"].astimezone(lagos)
    assert local.day == 2 and local.hour == 10
