"""Web feedback ownership contract tests (no live DB)."""

from wax.learner.signals import web_feedback_to_signal, SignalKind, record_explicit_interaction_evidence
from uuid import uuid4
import asyncio


def test_feedback_signal_is_not_durable_preference():
    sig = web_feedback_to_signal("up", response_ref=str(uuid4()))
    assert sig.kind == SignalKind.POSITIVE
    assert sig.strength < 0.9
    assert sig.metadata.get("response_ref")
    assert sig.source == "web_feedback"


def test_neutral_without_direction():
    assert web_feedback_to_signal("maybe", response_ref="x").kind == SignalKind.NEUTRAL


def test_conversational_source_cannot_use_explicit_recorder():
    from wax.learner.signals import DetectedSignal

    async def _run():
        sig = DetectedSignal(kind=SignalKind.POSITIVE, strength=0.8, source="conversational", subject="student")
        out = await record_explicit_interaction_evidence(object(), principal_id=uuid4(), signal=sig)
        assert out is None

    asyncio.run(_run())
