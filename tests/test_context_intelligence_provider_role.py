"""Contract: Context Intelligence does not silently use the primary tutor model."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_settings_declare_independent_ci_provider_fields():
    src = (ROOT / "wax/config/settings.py").read_text()
    for name in (
        "context_intelligence_provider",
        "context_intelligence_api_key",
        "context_intelligence_base_url",
        "context_intelligence_model",
        "context_intelligence_timeout_seconds",
        "context_intelligence_fallback_to_primary",
    ):
        assert name in src, f"missing setting {name}"


def test_providers_complete_accepts_context_role():
    src = (ROOT / "wax/intelligence/providers.py").read_text()
    assert 'role: str = "primary"' in src or 'role: str =' in src
    assert 'role == "context"' in src
    assert "self.context_model" in src


def test_agent_calls_complete_with_context_role():
    src = (ROOT / "wax/intelligence/context_intel/agent.py").read_text()
    assert 'role="context"' in src
    assert "allow_fallback=False" in src
