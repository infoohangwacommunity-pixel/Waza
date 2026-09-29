"""Cross-principal isolation and persistence (synthetic principals only)."""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture
def world_env(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setenv("WAX_WORKSPACE_ROOT", str(root))
    monkeypatch.setenv("WAX_DISABLE_BINARY_ALLOWLIST", "1")
    from wax.world import layout as ws
    from wax.world import manager as mgr

    monkeypatch.setattr(ws, "workspace_root", lambda: root)
    mgr._cache.clear()
    return root


def test_exec_echo(world_env):
    from wax.world.manager import get_or_create_world
    from wax.world.exec import world_exec

    async def _run():
        w = get_or_create_world("principal_a")
        return await world_exec(w, argv=["echo", "hello-world"])

    r = asyncio.run(_run())
    if r.get("error") in ("ISOLATION_UNAVAILABLE",) or "sandbox" in str(r.get("error") or "").lower():
        pytest.skip(str(r))
    assert r.get("ok") is True or "hello" in (r.get("stdout") or "")


def test_principal_b_cannot_read_principal_a_file(world_env):
    from wax.world.manager import create_world
    from wax.world.files import write_file
    from wax.world.layout import resolve_under_world

    a = create_world("principal_a", import_prior=False)
    b = create_world("principal_b", import_prior=False)
    write_file(a, "workspace/secret.txt", b"only-a")
    assert a.root != b.root
    path_b = resolve_under_world(b.root, "workspace/secret.txt")
    assert not path_b.exists()
    a_file = resolve_under_world(a.root, "workspace/secret.txt")
    assert a_file.read_bytes() == b"only-a"
    assert not str(a_file).startswith(str(b.root))


def test_world_survives_cache_clear(world_env):
    from wax.world.manager import create_world, get_or_create_world
    from wax.world import manager as mgr
    from wax.world.files import write_file, read_file

    w = create_world("principal_persist", import_prior=False)
    write_file(w, "workspace/keep.txt", b"durable")
    mgr._cache.clear()
    w2 = get_or_create_world("principal_persist")
    assert w2.root == w.root
    from wax.world.layout import resolve_under_world
    assert resolve_under_world(w2.root, "workspace/keep.txt").read_bytes() == b"durable"
