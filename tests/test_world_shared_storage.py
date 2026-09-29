"""Shared World storage contract for Web and Worker.

This test proves the actual World filesystem layout, transcript archive,
notebook area, and local artifact storage are reachable through one
configured workspace root. It does not assume two separate Railway Volumes
magically share files. The shared source of truth is one durable World tree
that both Web and Worker point at.

"""
from __future__ import annotations

import json
import uuid

import pytest

from wax.artifacts.storage import LocalStorage
from wax.world import layout, manager
from wax.world.transcript import record_message, read_transcript, transcript_path


@pytest.fixture
def shared_world_root(tmp_path, monkeypatch):
    """One durable workspace root that stands in for the shared World store."""
    ws_root = tmp_path / "shared-wax-workspaces"
    monkeypatch.setenv("WAX_WORKSPACE_ROOT", str(ws_root))
    monkeypatch.setattr(layout, "workspace_root", lambda: ws_root)
    monkeypatch.setenv("WAX_ARTIFACT_ROOT", str(ws_root / "wax-artifacts"))
    manager._cache.clear()
    import wax.world.transcript as tr

    tr._offset_cache.clear()
    yield ws_root
    manager._cache.clear()
    tr._offset_cache.clear()


def _write_world_files(world):
    """Write representative durable World artifacts the AI and infra rely on."""
    identity = {
        "world_id": world.world_id,
        "principal_id": world.principal_id,
        "created_at": 1_700_000_000.0,
        "schema_version": layout.SCHEMA_VERSION,
    }
    layout.write_json(world.root / "identity.json", identity)

    layout.write_json(
        world.root / "lifecycle.json",
        {"state": "READY", "reason": "", "updated_at": 1_700_000_000.0},
    )

    layout.write_json(
        world.root / "notebook" / "notes.json",
        {"author": "ai", "entries": [{"kind": "fact", "text": "student prefers algebra"}]},
    )

    (world.root / "workspace" / "projects" / "sample.txt").write_text("project content", encoding="utf-8")

    (world.root / "history" / "turn-summary.json").write_text(json.dumps({"turns": 1}), encoding="utf-8")


def test_world_written_by_one_service_readable_by_another(
    shared_world_root, monkeypatch
):
    """One World tree written through the configured root is readable from it."""
    monkeypatch.delenv("WAX_WORKSPACE_ROOT", raising=False)

    principal_id = str(uuid.uuid4())
    world = manager.create_world(principal_id, import_prior=False)
    _write_world_files(world)

    mid = uuid.uuid4()
    record_message(
        world.root,
        message_id=mid,
        content="student message",
        role="user",
        direction="inbound",
        channel="telegram",
        created_at=None,
        work_id=None,
        conversation_id=None,
        principal_id=principal_id,
        external_id="in-1",
    )

    # Simulate the other service resolving the SAME workspace root and loading
    # the World from disk. This is the contract Web and Worker must share.
    other_root = layout.workspace_root()
    assert other_root == shared_world_root
    idx = layout.read_json(
        layout.worlds_root() / ".principal_index" / f"{manager._safe(principal_id)}.json"
    )
    assert idx and idx.get("world_id")
    other_world = manager._load_from_disk(str(idx["world_id"]))
    assert other_world is not None
    assert other_world.world_id == world.world_id
    assert other_world.principal_id == principal_id

    # Durable filesystem areas must be present and readable.
    assert (other_world.root / "identity.json").is_file()
    assert (other_world.root / "notebook" / "notes.json").is_file()
    assert (other_world.root / "workspace" / "projects" / "sample.txt").is_file()
    assert (other_world.root / "history" / "turn-summary.json").is_file()
    assert transcript_path(other_world.root).is_file()

    rows = read_transcript(other_world.root)
    assert len(rows) == 1
    assert rows[0]["message_id"] == str(mid)
    assert rows[0]["content"] == "student message"
    assert rows[0]["channel"] == "telegram"
    assert rows[0]["direction"] == "inbound"


def test_artifact_storage_uses_configured_root(shared_world_root, monkeypatch):
    """Local artifacts land under the configured store, not a hidden /tmp."""
    monkeypatch.setenv("WAX_ARTIFACT_ROOT", str(shared_world_root / "wax-artifacts"))
    store = LocalStorage()
    assert store.root == shared_world_root / "wax-artifacts"
    uri = store.put("p1/artifact.txt", b"artifact payload")
    assert uri.startswith("local://")
    assert store.get("p1/artifact.txt") == b"artifact payload"
    assert store.exists("p1/artifact.txt")
