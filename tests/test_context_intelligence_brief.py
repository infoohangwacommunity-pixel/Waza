"""Pure tests for Context Brief — no DB/provider required."""

from wax.intelligence.context_intel.brief import (
    BriefItem,
    ContextBrief,
    brief_to_tutor_text,
)


def test_brief_distinguishes_fact_and_inference():
    brief = ContextBrief(
        request_understanding="Help with a problem",
        task_intent="teach",
        items=[
            BriefItem(kind="fact", text="Goal: pass exam", source="goals", confidence="high"),
            BriefItem(
                kind="inference",
                text="May need more practice on this topic",
                source="evidence",
                confidence="low",
            ),
        ],
    )
    text = brief_to_tutor_text(brief)
    assert "Facts / evidence" in text
    assert "Inferences" in text
    assert "provisional" in text.lower() or "not durable" in text.lower()


def test_no_context_required_block():
    brief = ContextBrief(no_context_required=True)
    text = brief_to_tutor_text(brief)
    assert "No learner-specific context" in text


def test_insufficient_evidence_flag():
    brief = ContextBrief(insufficient_evidence=True, request_understanding="Why wrong?")
    text = brief_to_tutor_text(brief)
    assert "insufficient evidence" in text.lower()
