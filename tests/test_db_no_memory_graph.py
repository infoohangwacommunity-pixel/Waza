"""DB has durable Memory only — no episode/link graph engine."""

from pathlib import Path


def test_no_memory_episode_link_classes():
    src = Path("wax/db/models.py").read_text()
    assert "class MemoryEpisode" not in src
    assert "class MemoryLink" not in src
    assert "class Memory(" in src
    assert "embedding" not in src or "Mapped" not in src.split("embedding")[0][-20:] if "embedding" in src else True


def test_no_embedding_column_on_memory():
    src = Path("wax/db/models.py").read_text()
    # Memory block should not declare embedding
    start = src.find("class Memory(")
    end = src.find("class Artifact(", start)
    block = src[start:end]
    assert "embedding" not in block
    assert "episode_id" not in block
    assert "layer:" not in block or "Mapped" not in block
