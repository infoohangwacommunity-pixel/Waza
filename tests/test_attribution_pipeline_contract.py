"""
Pipeline contract: natural language never becomes evidence via infrastructure.

The only infrastructure-written student evidence is explicit Web feedback
after ownership checks. Conversational meaning is the tutor's job.

These tests lock the contract so a future keyword detector cannot sneak back
into the worker post-turn path or signal module.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


FORBIDDEN_CALLS = {"detect_signals", "record_signals_as_evidence"}


def _call_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def test_worker_does_not_call_keyword_signal_detectors():
    names = _call_names(ROOT / "wax/workers/main.py")
    assert not (names & FORBIDDEN_CALLS)


def test_signals_module_has_no_keyword_detector():
    src = (ROOT / "wax/learner/signals.py").read_text()
    tree = ast.parse(src)
    defs = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "detect_signals" not in defs
    assert "record_signals_as_evidence" not in defs


def test_worker_source_has_no_substring_preference_rules():
    src = (ROOT / "wax/workers/main.py").read_text()
    # The post-turn path must not contain lexical preference assignment
    assert "likes_examples" not in src
    assert "prefers_examples" not in src
    assert 'if "happy" in' not in src
    assert 'if "example" in' not in src


ADVERSARIAL = [
    "My friend was happy with your explanation.",
    "My friend said your examples were helpful.",
    'My friend said, "I love your explanations."',
    "My teacher said I learn better with diagrams.",
    "Do examples help?",
    "Would examples help me?",
    "If I preferred examples, would that make learning easier?",
    "Imagine I hated examples.",
    "I don't understand why my friend likes examples.",
    "My friend told me I should ask you for examples.",
]


def test_adversarial_utterances_are_not_auto_classified_by_infrastructure():
    """Infrastructure has no classifier that maps these strings to evidence."""
    from wax.learner.signals import web_feedback_to_signal, SignalKind

    for text in ADVERSARIAL:
        # web_feedback_to_signal is for thumbs, not conversation
        sig = web_feedback_to_signal(text)
        assert sig.kind == SignalKind.NEUTRAL, text
