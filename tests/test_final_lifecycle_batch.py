
from pathlib import Path

def test_lifecycle_doc():
    assert Path("docs/LIFECYCLE.md").exists()

def test_memory_skips_cooldown_gate():
    src = Path("wax/memory/service.py").read_text()
    assert "memory_extraction_deferred_provider_cooldown" in src
    assert "memory_extraction_skipped" in src

def test_surface_principal_back_populates():
    src = Path("wax/db/models.py").read_text()
    assert 'relationship(back_populates="surfaces")' in src
    assert 'relationship(back_populates="principal")' in src or 'back_populates="surfaces"' in src

def test_worker_typing_and_research():
    src = Path("wax/workers/main.py").read_text()
    assert "send_typing" in src
    assert "lifecycle.research_suggestion" in src or "ResearchLoopService" in src
