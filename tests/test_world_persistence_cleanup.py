"""World cleanup must not age-delete durable student state."""

from __future__ import annotations

from pathlib import Path


def test_cleanup_only_targets_tmp_cache():
    src = Path("wax/world/cleanup.py").read_text()
    assert "durable_preserved" in src
    assert "shutil.rmtree(wdir" not in src
    assert "Never remove the world directory" in src or "MUST NOT" in src


def test_no_world_expiry_setting_as_durability_kill():
    settings = Path("wax/config/settings.py").read_text()
    assert "workspace_tmp_ttl_hours" in settings


def test_layout_contract_mentions_durable_volume():
    layout = Path("wax/world/layout.py").read_text()
    assert "durable" in layout.lower()
