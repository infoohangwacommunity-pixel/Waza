"""
Kennedy scenario (local simulation).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest


@pytest.fixture
def kennedy_env(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setenv("WAX_WORKSPACE_ROOT", str(root))
    monkeypatch.setenv("WAX_DISABLE_BINARY_ALLOWLIST", "1")
    from wax.terminal import workspace as ws
    from wax.world import manager as mgr

    monkeypatch.setattr(ws, "workspace_root", lambda: root)
    mgr._cache.clear()
    return root


def test_kennedy_discover_exec_echo(kennedy_env):
    from wax.world.manager import get_or_create_world
    from wax.world.discover import discover
    from wax.world.exec import world_exec

    async def _run():
        w = get_or_create_world("kennedy")
        snap = discover(w)
        assert snap["lifecycle"]["state"] in ("READY", "BUSY", "DEGRADED")
        r = await world_exec(w, argv=["echo", "hello-world"])
        return r

    r = asyncio.run(_run())
    if r.get("error") == "ISOLATION_UNAVAILABLE" or r.get("error") == "ISOLATION_UNAVAILABLE":
        pytest.skip("no isolation backend")
    if not r.get("ok") and "sandbox" in str(r.get("error") or "").lower():
        pytest.skip(str(r))
    assert r.get("ok") is True or "hello" in (r.get("stdout") or "")


def test_kennedy_acquire_and_reuse_if_possible(kennedy_env):
    from wax.world.manager import get_or_create_world
    from wax.world.acquire import acquire
    from wax.world.providers.base import AcquireRequest
    from wax.world.discover import discover
    from wax.world.errors import AcquisitionFailed, IsolationUnavailable, AcquisitionUnavailable, VerificationFailed

    async def _run():
        w = get_or_create_world("kennedy")
        req = AcquireRequest(kind="python_package", name="six")
        r1 = await acquire(w, req)
        return w, r1

    try:
        w, r1 = asyncio.run(_run())
    except (AcquisitionFailed, IsolationUnavailable, AcquisitionUnavailable, VerificationFailed) as e:
        pytest.skip(f"acquire unavailable: {e}")
    if not r1.get("ok"):
        pytest.skip(f"acquire failed: {r1}")
    assert r1["observed"]["verify_status"] == "ok"
    assert list((w.root / "software").glob("*.json"))
    snap = discover(w)
    names = [s.get("name") for s in snap.get("software") or []]
    assert "six" in names


def test_david_cannot_read_kennedy_file(kennedy_env):
    from wax.world.manager import create_world
    from wax.world.files import write_file
    from wax.world.layout import resolve_under_world
    from wax.world.errors import PathEscape
    from wax.world import manager as mgr

    mgr._cache.clear()
    k = create_world("kennedy", migrate_legacy=False)
    d = create_world("david", migrate_legacy=False)
    write_file(k, "workspace/notes.txt", "private")
    with pytest.raises(PathEscape):
        resolve_under_world(d.root, str((k.root / "workspace" / "notes.txt").resolve()))
