
from pathlib import Path

def test_capability_specs_not_reimported_locally():
    src = Path("wax/intelligence/context_intel/agent.py").read_text()
    # Should import once at module top; no second import inside _model_investigate after CAPABILITY_SPECS use
    assert "from wax.intelligence.context_intel.capabilities import CAPABILITY_SPECS, ContextCapabilities" in src
    # Count local re-imports of CAPABILITY_SPECS alone
    assert src.count("from wax.intelligence.context_intel.capabilities import CAPABILITY_SPECS\n") == 0

def test_stage_media_helper_exists():
    src = Path("wax/terminal/workspace.py").read_text()
    assert "def stage_media_for_work" in src
    assert "def path_in_principal_scope" in src

def test_worker_stages_media():
    src = Path("wax/workers/main.py").read_text()
    assert "stage_media_for_work" in src
    assert "media_staged_for_agent" in src
