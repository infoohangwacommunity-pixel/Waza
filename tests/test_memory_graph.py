
"""Multi-layer episodic memory graph."""

from wax.memory.graph import layer_for_memory_type, LAYERS, DEFAULT_LAYER_BUDGET, MemoryGraphService


def test_type_to_layer_mapping():
    assert layer_for_memory_type("preference") == "relationship"
    assert layer_for_memory_type("learning") == "procedural"
    assert layer_for_memory_type("episodic") == "episodic"
    assert layer_for_memory_type("goal") == "goal"
    assert layer_for_memory_type("semantic") == "semantic"


def test_layers_and_budget_cover_graph():
    assert "episodic" in LAYERS
    assert "procedural" in LAYERS
    assert sum(DEFAULT_LAYER_BUDGET.values()) >= 15


def test_render_package_text_bounded():
    svc = MemoryGraphService.__new__(MemoryGraphService)
    pkg = {
        "layers": {
            "semantic": [{"content": "Knows algebra basics"}],
            "episodic": [{"content": "Worked on quadratics Tuesday"}],
            "relationship": [{"content": "Prefers short WhatsApp replies"}],
        },
        "episodes": [{"status": "open", "title": "Quad practice", "summary": "q4"}],
    }
    text = MemoryGraphService.render_package_text(svc, pkg, max_chars=500)
    assert "Memory graph" in text
    assert "algebra" in text or "quadratics" in text or "WhatsApp" in text


def test_migration_and_capability_exist():
    from pathlib import Path
    assert Path("alembic/versions/002_memory_graph.py").exists()
    caps = Path("wax/intelligence/context_intel/capabilities.py").read_text()
    assert "inspect_memory_graph" in caps
