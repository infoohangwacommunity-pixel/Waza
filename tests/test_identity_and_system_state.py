"""WAX identity + dynamic system state (no vendor-as-identity)."""

from pathlib import Path


def test_tutor_system_is_wax_not_vendor():
    src = Path("wax/intelligence/tutor.py").read_text()
    assert "You are WAX —" in src or "You are WAX" in src
    assert "WAX Prep is the product" in src or "created by WAX Prep" in src
    assert "Privacy and memory (truthful)" in src
    # Must not claim to be the product company as the only name without WAX tutor name
    assert "You are WAX Prep — a persistent tutor" not in src


def test_ci_is_system_awareness_not_prediction():
    src = Path("wax/intelligence/context_intel/agent.py").read_text()
    assert "SYSTEM AWARENESS" in src
    assert "not predicting the student" in src.lower() or "not predicting" in src
    assert "render_system_state_for_model" in src


def test_system_state_module_identity_rule():
    src = Path("wax/intelligence/system_state.py").read_text()
    assert 'tutor_name": "WAX"' in src or "tutor_name" in src
    assert "WAX Prep" in src
    assert "Never introduce yourself as the model" in src or "identity_rule" in src


def test_system_state_injected_in_tutor():
    src = Path("wax/intelligence/tutor.py").read_text()
    assert "render_system_state_for_model" in src
    assert "system_state_inject_failed" in src
