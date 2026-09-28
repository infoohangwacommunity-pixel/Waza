"""Single World execution path: files persist; no terminal package; no allowlist."""

from __future__ import annotations

from pathlib import Path


def test_no_terminal_package():
    assert not Path("wax/terminal").exists()


def test_single_exec_entrypoints():
    ops = Path("wax/world/ops.py").read_text()
    assert "async def world_exec" in ops
    assert "ALLOWED_BINARIES" not in ops
    # free-form uses general shell, not a prefix allowlist
    assert "startswith(x) for x in" not in ops


def test_file_persistence_across_manager_reload(tmp_path, monkeypatch):
    from wax.world import layout as ws
    from wax.world import manager as mgr
    from wax.world.files import write_file, read_file
    from wax.world.manager import create_world, get_or_create_world

    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setattr(ws, "workspace_root", lambda: root)
    mgr._cache.clear()
    w1 = create_world("student-persist", migrate_legacy=False)
    write_file(w1, "workspace/notes.txt", "survives restart")
    wid = w1.world_id
    mgr._cache.clear()
    w2 = get_or_create_world("student-persist")
    assert w2.world_id == wid
    out = read_file(w2, "workspace/notes.txt")
    assert out.get("ok")
    assert "survives restart" in (out.get("text") or "")


def test_isolation_is_only_process_backend():
    iso = Path("wax/world/isolation.py").read_text()
    assert "ALLOWED_BINARIES" not in iso
    assert "run_isolated" in iso or "async def run_isolated" in iso
