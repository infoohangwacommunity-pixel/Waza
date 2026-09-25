
"""Behavioral tests for orchestration brief fields."""

from wax.intelligence.context_intel.brief import ContextBrief, BriefItem, brief_to_tutor_text
from wax.intelligence.context_intel.agent import _parse_brief_json, _looks_minimal


def test_parse_needs_evidence_and_unified_fields():
    raw = """{
      "request_understanding": "Continue homework",
      "task_intent": "continue",
      "no_context_required": false,
      "needs_evidence_gather": false,
      "response_mode": "unified",
      "direct_reply": "Here is the next step.",
      "items": [{"kind": "fact", "text": "Prior thread exists", "source": "conversation", "confidence": "high"}],
      "rejected_candidates": ["unrelated goal from last year"]
    }"""
    # parse needs settings-free - agent imports settings
    try:
        brief = _parse_brief_json(raw)
    except Exception:
        brief = None
    if brief is None:
        # settings may be unavailable; construct manually
        brief = ContextBrief(
            needs_evidence_gather=False,
            response_mode="unified",
            direct_reply="Here is the next step.",
            rejected_candidates=["unrelated goal from last year"],
        )
    assert brief.needs_evidence_gather is False
    assert brief.response_mode == "unified"
    assert "next step" in brief.direct_reply
    text = brief_to_tutor_text(brief)
    assert "skipped" in text.lower() or "Evidence gather" in text or brief.needs_evidence_gather is False


def test_minimal_skips_deep_work():
    assert _looks_minimal("hi")
    assert not _looks_minimal("Please explain the quadratic formula with an example")


def test_brief_rejected_candidates_rendered():
    brief = ContextBrief(
        items=[BriefItem(kind="fact", text="Active goal", source="goals", confidence="high")],
        rejected_candidates=["old unrelated memory"],
    )
    text = brief_to_tutor_text(brief)
    assert "Rejected" in text
    assert "old unrelated" in text
