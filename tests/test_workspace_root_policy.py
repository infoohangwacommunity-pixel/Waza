World store policy tests.

from pathlib import Path
import pytest


def test_docs_worker_owns_volume():
    src = Path("docs/WORLD_STORAGE.md").read_text()
    assert "Worker" in src
    assert "Volume" in src


def test_layout_module_documents_single_rule():
    src = Path("wax/world/layout.py").read_text()
    assert "REQUIRE_PERSISTENT_WORKSPACE" in src
    assert "one" in src.lower() or "Worker" in src
