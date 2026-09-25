"""
Attribution tests: keyword presence must NEVER become student evidence.

These tests lock the architectural rule that:
  - third-person statements are not student evidence
  - quotations are not student evidence
  - questions are not durable preferences
  - hypotheticals are not student preferences
  - current requests are not permanent preferences
  - only explicit UI feedback (web thumbs) may create interaction evidence
    and even that is scoped, moderate weight, not_yet_durable_preference

Conversational natural language is interpreted by intelligence, not by this module.
"""

from __future__ import annotations

import pytest

from wax.learner.signals import (
    ATTRIBUTION_FALSE_POSITIVE_CASES,
    ATTRIBUTION_CANDIDATE_CASES,
    SignalKind,
    web_feedback_to_signal,
    record_explicit_interaction_evidence,
)


def test_module_has_no_detect_signals_keyword_path():
    """Regression: the forbidden keyword detector must not exist."""
    import wax.learner.signals as mod

    assert not hasattr(mod, "detect_signals"), "keyword detect_signals must not exist"
    assert not hasattr(mod, "record_signals_as_evidence"), "record_signals_as_evidence must not exist"
    assert hasattr(mod, "web_feedback_to_signal")
    assert hasattr(mod, "record_explicit_interaction_evidence")


def test_false_positive_cases_documented():
    """Documented cases that must never auto-create student preference evidence."""
    assert len(ATTRIBUTION_FALSE_POSITIVE_CASES) >= 10
    for text in ATTRIBUTION_FALSE_POSITIVE_CASES:
        assert isinstance(text, str) and len(text) > 5
        # No production path converts these into evidence via keywords.
        # If a future detector is added it must fail these cases.


def test_candidate_cases_are_not_automatic_durable():
    """Candidate cases require intelligence; labels only describe expectation."""
    for text, kind, subject, stance in ATTRIBUTION_CANDIDATE_CASES:
        assert subject == "student"
        assert stance in ("assertion", "request", "question")
        # request stance must never be treated as durable preference by infrastructure
        if stance == "request":
            assert kind == "request"


def test_web_feedback_positive():
    sig = web_feedback_to_signal("up", response_ref="msg-123")
    assert sig.kind == SignalKind.POSITIVE
    assert sig.subject == "student"
    assert sig.source == "web_feedback"
    assert sig.strength < 0.9  # not automatic durable
    assert sig.metadata.get("response_ref") == "msg-123"
    assert sig.metadata.get("direction") == "up"


def test_web_feedback_negative():
    sig = web_feedback_to_signal("down", response_ref="work-abc")
    assert sig.kind == SignalKind.NEGATIVE
    assert sig.metadata.get("direction") == "down"


def test_web_feedback_neutral_without_direction():
    sig = web_feedback_to_signal("", response_ref="x")
    assert sig.kind == SignalKind.NEUTRAL


def test_web_feedback_requires_response_ref_for_recording_contract():
    """Contract: API path only records when response_ref is present."""
    sig = web_feedback_to_signal("up", response_ref=None)
    # signal can be built, but API must refuse recording without ref
    assert sig.kind == SignalKind.POSITIVE
    # the API layer checks ref before calling record_explicit_interaction_evidence


@pytest.mark.asyncio
async def test_record_explicit_skips_non_web_and_neutral(monkeypatch):
    """Only web_feedback + non-neutral + subject=student may record."""
    from wax.learner.signals import DetectedSignal

    class FakeSession:
        pass

    # neutral → None
    sig = DetectedSignal(kind=SignalKind.NEUTRAL, strength=0.0, source="web_feedback")
    out = await record_explicit_interaction_evidence(
        FakeSession(), principal_id=__import__("uuid").uuid4(), signal=sig
    )
    assert out is None

    # conversational source → None (must go through intelligence)
    sig2 = DetectedSignal(
        kind=SignalKind.POSITIVE, strength=0.7, source="conversational", subject="student"
    )
    out2 = await record_explicit_interaction_evidence(
        FakeSession(), principal_id=__import__("uuid").uuid4(), signal=sig2
    )
    assert out2 is None
