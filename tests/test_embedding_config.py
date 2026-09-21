"""Embeddings must use EMBEDDING_* base URL, not silently the chat provider."""

from pathlib import Path


def test_settings_have_embedding_base_url():
    src = Path("wax/config/settings.py").read_text()
    assert "embedding_base_url" in src
    assert "embedding_provider" in src
    assert "embedding_api_key" in src


def test_embedding_config_reads_dedicated_settings():
    src = Path("wax/memory/embeddings.py").read_text()
    assert "EMBEDDING_BASE_URL" in src
    assert "embedding_api_key" in src
    assert "api.voyageai.com" in src  # hint only when provider=voyage
    # Must not default to primary_base_url for embeddings anymore
    assert "settings.primary_api_key" not in src or "embedding" in src
    assert "primary_base_url" not in src


def test_no_silent_primary_chat_for_embeddings():
    src = Path("wax/memory/embeddings.py").read_text()
    assert "do NOT silently" in src or "No dedicated embedding key" in src
