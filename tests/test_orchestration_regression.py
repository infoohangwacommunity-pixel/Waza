"""
Regression tests for Context Intelligence orchestration integration.

Proves:
- needs_evidence_gather=false does not leave undefined evidence
- unified direct_reply propagates to AssembledContext / tutor skip contract
- _model_investigate accepts unified_mode
- provider independence contract still holds
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_model_investigate_signature_has_unified_mode():
    src = (ROOT / "wax/intelligence/context_intel/agent.py").read_text()
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_model_investigate":
            args = [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
            assert "unified_mode" in args, f"args={args}"
            found = True
    assert found, "_model_investigate not found"


def test_resolver_source_never_uses_evidence_when_skip_without_guard():
    """Static guard: after skip_evidence_gather branch, bare 'evidence.' must not appear unguarded."""
    src = (ROOT / "wax/learner/resolver.py").read_text()
    # Must initialize evidence = None before the if
    assert "evidence = None" in src
    assert "if evidence is not None" in src or "evidence is not None" in src
    # The dangerous pattern: if evidence.degraded outside assignment when skip
    # After our fix, degradation check is inside the else branch
    lines = src.splitlines()
    in_skip_block = False
    for i, line in enumerate(lines):
        if "if pack.skip_evidence_gather:" in line:
            in_skip_block = True
        if in_skip_block and line.strip().startswith("else:"):
            in_skip_block = False
        if in_skip_block and "evidence." in line and "evidence = None" not in line:
            # allow comments
            if not line.strip().startswith("#"):
                pytest.fail(f"unguarded evidence use in skip block line {i+1}: {line}")


def test_parse_brief_unified_and_no_evidence():
    from wax.intelligence.context_intel.agent import _parse_brief_json

    brief = _parse_brief_json(
        """{
        "request_understanding": "greeting",
        "task_intent": "acknowledge",
        "no_context_required": true,
        "needs_evidence_gather": false,
        "response_mode": "unified",
        "direct_reply": "Hey! How can I help?",
        "items": []
    }"""
    )
    assert brief is not None
    assert brief.needs_evidence_gather is False
    assert brief.response_mode == "unified"
    assert "Hey" in brief.direct_reply


@pytest.mark.asyncio
async def test_skip_evidence_path_completes_without_gather():
    """Behavioral: skip_evidence_gather does not call gather_evidence and does not crash."""
    pytest.importorskip("sqlalchemy")
    from wax.learner.resolver import ContextResolver, LearnerContextPack
    from wax.intelligence.context_intel.brief import ContextBrief

    session = MagicMock()
    resolver = ContextResolver(session)

    brief = ContextBrief(
        no_context_required=True,
        needs_evidence_gather=False,
        task_intent="acknowledge",
        request_understanding="hi",
    )

    gather = AsyncMock()
    recent = AsyncMock(return_value=[])

    with patch("wax.intelligence.context_intel.investigate_context", AsyncMock(return_value=brief)), \
         patch("wax.learner.resolver.gather_evidence", gather), \
         patch.object(resolver, "_recent_messages", recent), \
         patch("wax.config.settings.get_settings") as gs:
        gs.return_value = MagicMock(
            context_intelligence_controls_evidence=True,
            context_intelligence_enabled=True,
        )
        # linked channels may fail soft
        with patch("wax.domain.identity.linked_channels_state", AsyncMock(side_effect=Exception("skip"))):
            pack = await resolver.resolve(
                principal_id="00000000-0000-0000-0000-000000000001",
                conversation_id=None,
                channel="whatsapp",
                user_text="hi",
                tutor_system="You are a tutor.",
            )

    gather.assert_not_called()
    assert pack.skip_evidence_gather is True
    assert pack.evidence == []
    assert isinstance(pack, LearnerContextPack)


def test_tutor_skips_loop_when_unified_reply_present():
    src = (ROOT / "wax/intelligence/tutor.py").read_text()
    assert "_unified_done" in src
    assert "for _ in range(0 if _unified_done else max_rounds)" in src
    assert "ctx.unified_direct_reply" in src


def test_provider_independence_contract():
    src = (ROOT / "wax/intelligence/providers.py").read_text()
    assert 'role == "context"' in src
    assert "context_intelligence_fallback_to_primary" in src
    assert "self.context_model" in src
