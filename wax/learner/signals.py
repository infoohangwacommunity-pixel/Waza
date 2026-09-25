"""
Learner signals — architectural boundary only.

CRITICAL RULE (Waza philosophy):
  A keyword or phrase is NEVER student evidence by itself.
  Third-person, quotation, question, hypothetical, negation, and
  current-request language must NOT become durable preferences.

Natural conversational meaning is interpreted by the tutor intelligence
(memory extraction, EvidencePlanner, record_evidence tool). Infrastructure
only stores what the intelligence (or an explicit UI action) decides is
candidate evidence, with correct attribution, directness, and weight.

This module provides:
  - Explicit Web feedback mapping (thumbs are direct interaction evidence)
  - Helpers for tests that document attribution expectations
  - NO keyword → Evidence path

See docs/EVIDENCE_ARCHITECTURE.md and the Evidence → Hypothesis → Memory hierarchy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import UUID


def _log():
    from wax.observability.logging import get_logger
    return get_logger(__name__)


class SignalKind(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    PREFERENCE = "preference"
    CHECKIN_RESPONSE = "checkin_response"
    REQUEST = "request"  # current request, not durable preference
    NEUTRAL = "neutral"


@dataclass
class DetectedSignal:
    """Structured signal after semantic interpretation (never from bare keywords)."""

    kind: SignalKind
    strength: float  # 0..1
    labels: list[str] = field(default_factory=list)
    subject: str = "student"  # student | other | unclear
    stance: str = "assertion"  # assertion | question | request | quote | hypothetical | negation
    raw_span: str = ""
    source: str = "conversational"  # conversational | web_feedback | checkin
    metadata: dict[str, Any] = field(default_factory=dict)


def web_feedback_to_signal(
    direction: str,
    *,
    response_ref: str | None = None,
    work_id: str | None = None,
    message_id: str | None = None,
) -> DetectedSignal:
    """
    Explicit UI feedback (👍 / 👎) is direct interaction evidence about a
    specific response. Still NOT an automatic permanent preference.
    """
    d = (direction or "").lower().strip()
    meta = {
        "direction": None,
        "response_ref": response_ref,
        "work_id": work_id,
        "message_id": message_id,
    }
    if d in ("up", "thumbsup", "positive", "helpful", "1", "👍"):
        meta["direction"] = "up"
        return DetectedSignal(
            kind=SignalKind.POSITIVE,
            strength=0.55,  # moderate; needs repetition for durable preference
            labels=["web_response_feedback"],
            subject="student",
            stance="assertion",
            source="web_feedback",
            metadata=meta,
        )
    if d in ("down", "thumbsdown", "negative", "unhelpful", "0", "👎"):
        meta["direction"] = "down"
        return DetectedSignal(
            kind=SignalKind.NEGATIVE,
            strength=0.55,
            labels=["web_response_feedback"],
            subject="student",
            stance="assertion",
            source="web_feedback",
            metadata=meta,
        )
    return DetectedSignal(kind=SignalKind.NEUTRAL, strength=0.0, source="web_feedback", metadata=meta)


async def record_explicit_interaction_evidence(
    session,
    *,
    principal_id: UUID,
    signal: DetectedSignal,
    work_id: UUID | None = None,
    message_id: UUID | None = None,
    conversation_id: UUID | None = None,
    surface_id: UUID | None = None,
) -> Any | None:
    """
    Record only *explicit* interaction evidence (e.g. web thumbs).
    Conversational natural language must go through intelligence, not this path.
    """
    if signal.kind == SignalKind.NEUTRAL or signal.source != "web_feedback":
        return None
    if signal.subject != "student":
        return None

    from wax.memory.evidence import EvidenceService

    svc = EvidenceService(session)
    direction = (signal.metadata or {}).get("direction")
    claim = f"interaction_feedback:{direction or 'unknown'}"
    try:
        ev = await svc.record(
            principal_id=principal_id,
            evidence_type="self_report",
            description=f"Explicit response feedback ({direction})",
            claim_key=claim,
            payload={
                "labels": signal.labels,
                "strength": signal.strength,
                "source": signal.source,
                "subject": signal.subject,
                "stance": signal.stance,
                "response_ref": (signal.metadata or {}).get("response_ref"),
                "work_id": str(work_id) if work_id else (signal.metadata or {}).get("work_id"),
                "message_id": str(message_id) if message_id else (signal.metadata or {}).get("message_id"),
                "surface_id": str(surface_id) if surface_id else None,
                "not_yet_durable_preference": True,
                "scope": "this_response",
            },
            assistance_level="unknown",
            weight=float(signal.strength),
            directness=0.85,  # explicit UI action is direct
            independence=0.9,
            specificity=0.7,
            source="web_feedback",
            observation_id=None,
            work_id=work_id,
        )
        _log().info(
            "explicit_interaction_evidence",
            principal_id=str(principal_id),
            direction=direction,
            claim=claim,
        )
        return ev
    except Exception:
        _log().exception("explicit_interaction_evidence_failed")
        return None


# ---------------------------------------------------------------------------
# Attribution test helpers (documentation + tests only — no production evidence)
# ---------------------------------------------------------------------------

# Cases that MUST NOT produce student preference evidence from keywords alone.
ATTRIBUTION_FALSE_POSITIVE_CASES = [
    "My friend was happy with your explanation.",
    "My friend said examples helped him.",
    "My brother doesn't understand this.",
    "My teacher said 'this is easy'.",
    "My friend said 'I love your explanations'.",
    "My mother thinks I'm good at maths.",
    "Do examples help?",
    "Would examples help me?",
    "Why do people use examples?",
    "If someone preferred diagrams, would that help them?",
    "Imagine I was someone who hated examples…",
    "I don't understand why my friend likes biology.",
    "My friend said I should ask you for examples.",
    "What does example mean?",
]

# Cases that MAY become candidate evidence only after semantic interpretation
# by intelligence (subject=student, stance appropriate). Never automatic durable.
ATTRIBUTION_CANDIDATE_CASES = [
    ("I really liked the example you used.", "positive", "student", "assertion"),
    ("I don't like examples. They confuse me.", "negative", "student", "assertion"),
    ("I don't understand this.", "negative", "student", "assertion"),  # current difficulty
    ("Can you give me an example?", "request", "student", "request"),  # current request only
    ("That explanation finally made sense to me.", "positive", "student", "assertion"),
    ("I'm happy because I finally understand your explanation.", "positive", "student", "assertion"),
]
