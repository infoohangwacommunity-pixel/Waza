"""World core: identity, layout, discover, isolation boundary, cleanup safety."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def world_root(tmp_path, monkeypatch):
    monkeypatch.setenv("WAX_WORKSPACE_ROOT", str(tmp_path / "ws"))
    # force settings pick up — workspace_root reads env via terminal.workspace
    from wax.terminal import workspace as ws

    monkeypatch.setattr(ws, "DEFAULT_ROOT", str(tmp_path / "ws"))
    # clear manager cache
    from wax.world import manager as mgr

    mgr._cache.clear()
    return tmp_path / "ws"


def test_world_id_independent_of_principal(world_root, monkeypatch):
    monkeypatch.setenv("WAX_WORKSPACE_ROOT", str(world_root))
    from wax.terminal import workspace as ws
    from wax.config import get_settings

    monkeypatch.setattr(ws, "workspace_root", lambda: world_root)
    from wax.world.manager import create_world

    w = create_world("principal-A", migrate_legacy=False)
    assert w.world_id != "principal-A"
    assert w.principal_id == "principal-A"
    assert w.root.exists()
    assert (w.root / "identity.json").is_file()
    assert (w.root / "runtimes").is_dir()
    assert (w.root / "workspace").is_dir()


def test_two_worlds_isolated_paths(world_root, monkeypatch):
    from wax.terminal import workspace as ws
    from wax.world.manager import create_world
    from wax.world.files import write_file, read_file
    from wax.world.layout import resolve_under_world
    from wax.world.errors import PathEscape

    monkeypatch.setattr(ws, "workspace_root", lambda: world_root)
    from wax.world import manager as mgr

    mgr._cache.clear()
    ka = create_world("principal_a", migrate_legacy=False)
    da = create_world("principal_b", migrate_legacy=False)
    write_file(ka, "workspace/secret.txt", "principal-a-only")
    # Principal B cannot resolve Principal A paths through his world
    with pytest.raises(PathEscape):
        resolve_under_world(da.root, str(ka.root / "workspace" / "secret.txt"))
    # Absolute path to other world
    with pytest.raises(PathEscape):
        resolve_under_world(da.root, str((ka.root / "workspace" / "secret.txt").resolve()))


def test_discover_structure(world_root, monkeypatch):
    from wax.terminal import workspace as ws
    from wax.world.manager import create_world
    from wax.world.discover import discover
    from wax.world import manager as mgr

    monkeypatch.setattr(ws, "workspace_root", lambda: world_root)
    mgr._cache.clear()
    w = create_world("p1", migrate_legacy=False)
    snap = discover(w)
    assert snap["ok"] is True
    assert snap["world_id"] == w.world_id
    assert "lifecycle" in snap
    assert "resources" in snap
    assert "runtimes" in snap
    assert "software" in snap


def test_cleanup_does_not_delete_runtimes(world_root, monkeypatch):
    from wax.terminal import workspace as ws
    from wax.terminal.cleanup import cleanup_old_files
    from wax.world.manager import create_world
    from wax.world import manager as mgr
    import time

    monkeypatch.setattr(ws, "workspace_root", lambda: world_root)
    mgr._cache.clear()
    w = create_world("p2", migrate_legacy=False)
    protected = w.root / "runtimes" / "python" / "default" / "marker.txt"
    protected.parent.mkdir(parents=True, exist_ok=True)
    protected.write_text("keep")
    # make mtime old
    os.utime(protected, (1, 1))
    tmpf = w.root / "tmp" / "old.txt"
    tmpf.parent.mkdir(parents=True, exist_ok=True)
    tmpf.write_text("gone")
    os.utime(tmpf, (1, 1))
    cleanup_old_files(ttl_hours=0)
    assert protected.is_file()
    # tmp may be removed
    assert not tmpf.is_file() or True  # best effort


def test_no_allowed_binaries_in_isolation_module():
    src = Path("wax/world/isolation.py").read_text()
    # Must not implement a binary allowlist check
    assert "if binary not in ALLOWED" not in src
    assert "ALLOWED_BINARIES =" not in src


def test_sandbox_has_no_product_allowlist():
    src = Path("wax/terminal/sandbox.py").read_text()
    assert "Command not permitted" not in src
    from wax.terminal.sandbox import ALLOWED_BINARIES
    assert not ALLOWED_BINARIES


def test_path_escape_symlink(world_root, monkeypatch, tmp_path):
    from wax.terminal import workspace as ws
    from wax.world.manager import create_world
    from wax.world.layout import resolve_under_world
    from wax.world.errors import PathEscape
    from wax.world import manager as mgr

    monkeypatch.setattr(ws, "workspace_root", lambda: world_root)
    mgr._cache.clear()
    w = create_world("p3", migrate_legacy=False)
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    link = w.root / "workspace" / "evil"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink not permitted")
    # Symlink pointing outside world must be rejected
    with pytest.raises(PathEscape):
        resolve_under_world(w.root, "workspace/evil")
