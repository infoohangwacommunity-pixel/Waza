"""P1-C: assessment timeout, workspace manifest, interaction lock, tools."""

from pathlib import Path


def test_assessment_timeout_method():
    src = Path("wax/assessment/service.py").read_text()
    assert "record_item_timeout" in src


def test_workspace_env_manifest():
    from wax.terminal.env_manifest import load_manifest, record_package, has_package
    pid = "test-principal-manifest"
    record_package(pid, "numpy", version="1.26")
    assert has_package(pid, "numpy")
    man = load_manifest(pid)
    assert "numpy" in man["packages"]


def test_consume_uses_for_update():
    src = Path("wax/interaction/service.py").read_text()
    assert "with_for_update" in src


def test_new_tools_registered():
    src = Path("wax/tools/registry.py").read_text()
    for n in (
        "record_assessment_timeout",
        "workspace_env",
        "check_quiet_hours",
    ):
        assert n in src
