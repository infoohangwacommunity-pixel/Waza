"""
Internal evaluation criteria for WAX as a tutor.

These are engineering evaluation criteria — not rigid learner labels.
"""

from __future__ import annotations

RUBRIC_CRITERIA: list[dict[str, str]] = [
    {
        "id": "context",
        "name": "Maintain context",
        "question": "Does WAX track what the learner is currently working on across turns?",
    },
    {
        "id": "gaps",
        "name": "Detect gaps",
        "question": "Does WAX notice missing prerequisites or weak foundations?",
    },
    {
        "id": "misconceptions",
        "name": "Detect misconceptions",
        "question": "Does WAX distinguish wrong answers caused by wrong mental models?",
    },
    {
        "id": "adapt",
        "name": "Adapt explanations",
        "question": "Does WAX change approach when an explanation fails?",
    },
    {
        "id": "strategy_variety",
        "name": "Vary teaching strategy",
        "question": "Does WAX avoid a fixed Question-1 / Correct rhythm?",
    },
    {
        "id": "evidence",
        "name": "Use evidence",
        "question": "Are durable learning updates backed by observations?",
    },
    {
        "id": "retrieval",
        "name": "Retrieve appropriately",
        "question": "Does WAX use hints before revealing when the learner forgot?",
    },
    {
        "id": "transfer",
        "name": "Transfer knowledge",
        "question": "Does WAX eventually check application in a new context?",
    },
    {
        "id": "preferences",
        "name": "Respect preferences",
        "question": "Do durable prefs (short, fewer emojis) persist across turns?",
    },
    {
        "id": "tools",
        "name": "Use tools honestly",
        "question": "Does WAX only claim tool success when tools report success?",
    },
    {
        "id": "research",
        "name": "Research when fresh",
        "question": "Does WAX research when currency matters?",
    },
    {
        "id": "media",
        "name": "Handle media",
        "question": "Are voice notes transcribed and documents usable?",
    },
    {
        "id": "continuity",
        "name": "Longitudinal continuity",
        "question": "Can WAX recover useful state days later?",
    },
    {
        "id": "failure",
        "name": "Recover from failure",
        "question": "Do delivery/tool failures leave durable state intact?",
    },
]


def rubric_ids() -> list[str]:
    return [c["id"] for c in RUBRIC_CRITERIA]
