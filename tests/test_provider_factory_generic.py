"""Provider factory is name-agnostic for OpenAI-compatible APIs."""

from __future__ import annotations

import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _build():
    # Import after path is project root
    from wax.intelligence.providers import (
        OpenAICompatibleProvider,
        AnthropicProvider,
        _build_provider,
    )
    return OpenAICompatibleProvider, AnthropicProvider, _build_provider


def test_arbitrary_openai_compatible_name_with_base_url():
    OpenAICompatibleProvider, _, _build_provider = _build()
    p = _build_provider(
        "my_custom_llm",
        api_key="sk-test",
        model="custom-model",
        base_url="https://llm.example.com/v1",
        timeout=30.0,
    )
    assert p is not None
    assert isinstance(p, OpenAICompatibleProvider)
    assert p.name == "my_custom_llm"
    assert p.model == "custom-model"
    assert p.base_url == "https://llm.example.com/v1"
    assert p.api_key == "sk-test"


def test_groq_name_without_allowlist_hack():
    OpenAICompatibleProvider, _, _build_provider = _build()
    p = _build_provider(
        "groq",
        api_key="gsk_test",
        model="llama-3.3-70b-versatile",
        base_url="https://api.groq.com/openai/v1",
    )
    assert isinstance(p, OpenAICompatibleProvider)
    assert p.name == "groq"
    assert "groq.com" in p.base_url


def test_groq_convenience_base_url_when_omitted():
    OpenAICompatibleProvider, _, _build_provider = _build()
    p = _build_provider("groq", api_key="gsk_test", model="llama-3.1-8b-instant", base_url="")
    assert isinstance(p, OpenAICompatibleProvider)
    assert p.base_url.rstrip("/").endswith("/openai/v1")


def test_unknown_name_without_base_url_returns_none():
    _, _, _build_provider = _build()
    p = _build_provider("totally_unknown_vendor", api_key="k", model="m", base_url="")
    assert p is None


def test_none_or_empty_key_returns_none():
    _, _, _build_provider = _build()
    assert _build_provider("openai", api_key="", model="gpt-4o", base_url="https://api.openai.com/v1") is None
    assert _build_provider("none", api_key="sk", model="gpt-4o") is None


def test_anthropic_stays_native():
    _, AnthropicProvider, _build_provider = _build()
    p = _build_provider("anthropic", api_key="sk-ant", model="claude-3-5-sonnet", base_url="")
    assert isinstance(p, AnthropicProvider)


def test_factory_not_hardcoded_allowlist_only():
    """Regression: factory must not only accept openai/grok/openrouter."""
    src = (ROOT / "wax/intelligence/providers.py").read_text()
    assert 'if provider_name in ("openai", "grok", "openrouter")' not in src
    assert "OpenAI-compatible" in src or "openai_compatible" in src.lower() or "Any name" in src or "identifier" in src
