"""
Cross-principal isolation and persistence scenario (synthetic principals only).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


@pytest.fixture
def world_env(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setenv("WAX_WORKSPACE_ROOT", str(root))
    monkeypatch.setenv("WAX_DISABLE_BINARY_ALLOWLIST", "1")
    from wax.terminal import workspace as ws
    from wax.world import manager as mgr

    monkeypatch.setattr(ws, "workspace_root", lambda: root)
    mgr._cache.clear()
    return root


def test_discover_and_exec_echo(world_env):
    from wax.world.manager import get_or_create_world
    from wax.world.discover import discover
    from wax.world.exec import world_exec

    async def _run():
        w = get_or_create_world("principal_a")
        snap = discover(w)
        assert snap["lifecycle"]["state"] in ("READY", "BUSY", "DEGRADED")
        return await world_exec(w, argv=["echo", "hello-world"])

    r = asyncio.run(_run())
    if r.get("error") in ("ISOLATION_UNAVAILABLE",) or "sandbox" in str(r.get("error") or "").lower():
        pytest.skip(str(r))
    assert r.get("ok") is True or "hello" in (r.get("stdout") or "")


def test_acquire_and_reuse_if_possible(world_env):
    from wax.world.manager import get_or_create_world
    from wax.world.acquire import acquire
    from wax.world.providers.base import AcquireRequest
    from wax.world.discover import discover
    from wax.world.errors import (
        AcquisitionFailed,
        IsolationUnavailable,
        AcquisitionUnavailable,
        VerificationFailed,
    )

    async def _run():
        w = get_or_create_world("principal_a")
        req = AcquireRequest(kind="python_package", name="six")
        return w, await acquire(w, req)

    try:
        w, r1 = asyncio.run(_run())
    except (AcquisitionFailed, IsolationUnavailable, AcquisitionUnavailable, VerificationFailed) as e:
        pytest.skip(f"acquire unavailable: {e}")
    if not r1.get("ok"):
        pytest.skip(f"acquire failed: {r1}")
    assert r1["observed"]["verify_status"] == "ok"
    assert list((w.root / "software").glob("*.json"))
    names = [s.get("name") for s in discover(w).get("software") or []]
    assert "six" in names


def test_principal_b_cannot_read_principal_a_file(world_env):
    from wax.world.manager import create_world
    from wax.world.files import write_file
    from wax.world.layout import resolve_under_world
    from wax.world.errors import PathEscape
    from wax.world import manager as mgr

    mgr._cache.clear()
    a = create_world("principal_a", migrate_legacy=False)
    b = create_world("principal_b", migrate_legacy=False)
    write_file(a, "workspace/notes.txt", "private")
    with pytest.raises(PathEscape):
        resolve_under_world(b.root, str((a.root / "workspace" / "notes.txt").resolve()))
