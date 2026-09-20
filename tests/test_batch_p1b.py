from pathlib import Path


def test_tool_meta_exists():
    from wax.tools.meta import tool_meta, TOOL_META
    assert tool_meta("run_python")["risk"] == "high"
    assert "research_fetch" in TOOL_META


def test_research_module():
    assert Path("wax/research/fetch.py").exists()
    src = Path("wax/research/fetch.py").read_text()
    assert "_host_allowed" in src
    assert "169.254.169.254" in src


def test_whatsapp_interaction_consume():
    src = Path("wax/messaging/whatsapp/handler.py").read_text()
    assert "InteractionService" in src


def test_storage_effective_backend():
    src = Path("wax/config/settings.py").read_text()
    assert "effective_storage_backend" in src


def test_interaction_opaque_ids():
    src = Path("wax/interaction/service.py").read_text()
    assert "logical_id" in src
