Single intelligence path — no memory-model brain or ToolSpec.

from pathlib import Path


def test_no_memory_model_branch():
    src = Path("wax/intelligence/providers.py").read_text()
    assert "use_memory_model" not in src
    assert "self.memory_model" not in src
    assert "ToolSpec" not in src


def test_settings_no_side_brains():
    src = Path("wax/config/settings.py").read_text()
    assert "memory_provider" not in src
    assert "multimodal_provider" not in src
    assert "embedding_provider" not in src
    assert "primary_provider" in src
