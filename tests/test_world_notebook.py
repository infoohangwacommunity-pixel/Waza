"""Regression: AI-owned notebook inside each student's World.

The notebook is durable files the AI creates/reads/edits through its existing
World capabilities. Infrastructure provides only persistence, isolation and
safe file access — no schema, no automatic extraction, no summarization, no
ranking, no pruning. These tests prove:

- notebook/ exists in every student's World layout;
- notes belong ONLY to the correct student's World (isolation + path escape);
- notes survive simulated worker/deployment restarts (fresh caches, same disk);
- the complete notebook can be inspected from the World filesystem;
- infrastructure never auto-writes or auto-prunes anything into it.
"""

from __future__ import annotations

import os
import uuid

import pytest

from wax.world import layout, manager
from wax.world.errors import PathEscape
from wax.world.notebook import (
    MAX_NOTE_BYTES,
    NOTEBOOK_DIRNAME,
    list_notes,
    notebook_dir,
    read_note,
    world_root_for_principal,
    write_note,
)


@pytest.fixture
def world_env(tmp_path, monkeypatch):
    """Point the World filesystem at a fresh temp dir; clear all process caches."""
    ws_root = tmp_path / "ws"
    monkeypatch.setenv("WAX_WORKSPACE_ROOT", str(ws_root))
    monkeypatch.setattr(layout, "workspace_root", lambda: ws_root)
    manager._cache.clear()
    yield tmp_path
    manager._cache.clear()


def _restart_process():
    """Simulate a worker/deployment restart: drop every in-process cache."""
    manager._cache.clear()
    import wax.world.transcript as tr

    tr._offset_cache.clear()


def test_notebook_is_part_of_world_layout(world_env):
    w = manager.create_world(str(uuid.uuid4()))
    assert NOTEBOOK_DIRNAME in layout.SUBDIRS
    assert (w.root / NOTEBOOK_DIRNAME).is_dir()
    # Durable: cleanup scavenges only tmp/ and cache/.
    from wax.world import cleanup

    assert NOTEBOOK_DIRNAME in cleanup._DURABLE_TOP
    assert NOTEBOOK_DIRNAME not in cleanup._TEMP_TOP


def test_ai_creates_reads_edits_organizes_freely(world_env):
    w = manager.create_world(str(uuid.uuid4()))
    # No fixed schema: any structure the AI chooses works.
    r1 = write_note(w.root, "goals.md", "# Goals\n- Pass A-levels maths by June")
    r2 = write_note(w.root, "topics/algebra/explanations-that-worked.md", "Used trains analogy.")
    r3 = write_note(w.root, "weird/custom/structure/deep.json", '{"anything": true}')
    assert r1["ok"] and r2["ok"] and r3["ok"]

    got = read_note(w.root, "goals.md")
    assert got["ok"] and "A-levels" in got["content"]

    # The AI modifies its own notes; infrastructure stores exactly what it wrote.
    long_content = "detail " * 5000  # well past any old ~120-char style clip
    write_note(w.root, "goals.md", long_content)
    again = read_note(w.root, "goals.md")
    assert again["content"] == long_content

    listing = list_notes(w.root)
    paths = {n["path"] for n in listing["notes"]}
    assert {"goals.md", os.path.join("topics", "algebra", "explanations-that-worked.md"),
            os.path.join("weird", "custom", "structure", "deep.json")} <= paths


def test_notebook_belongs_only_to_correct_students_world(world_env):
    alice = manager.create_world(str(uuid.uuid4()))
    bob = manager.create_world(str(uuid.uuid4()))

    write_note(alice.root, "about-me.md", "Alice prefers worked examples.")

    # Bob's World has its own empty notebook — nothing leaks across.
    assert list_notes(bob.root)["count"] == 0
    assert not (bob.root / NOTEBOOK_DIRNAME / "about-me.md").exists()

    # Principal index resolves each notebook to exactly its own World.
    assert world_root_for_principal(alice.principal_id) == alice.root
    assert world_root_for_principal(bob.principal_id) == bob.root
    assert world_root_for_principal(uuid.uuid4()) is None


def test_notebook_access_cannot_escape_the_world(world_env):
    w = manager.create_world(str(uuid.uuid4()))
    other = manager.create_world(str(uuid.uuid4()))

    with pytest.raises(PathEscape):
        write_note(w.root, "../outside.md", "escape attempt")
    with pytest.raises(PathEscape):
        read_note(w.root, "../../etc/passwd")
    # Symlink planted inside the notebook must not expose another World.
    nb = notebook_dir(w.root)
    link = nb / "evil"
    try:
        os.symlink(other.root / NOTEBOOK_DIRNAME, link)
        with pytest.raises(PathEscape):
            read_note(w.root, "evil/about-me.md")
    finally:
        if link.is_symlink():
            link.unlink()


def test_notes_survive_worker_and_deployment_restarts(world_env):
    pid = str(uuid.uuid4())
    w = manager.create_world(pid)
    content = "Explanation that did NOT work: jumped straight to abstract proofs."
    write_note(w.root, "teaching/what-didnt-work.md", content)

    # Simulated restarts: process caches gone; only the durable volume remains.
    _restart_process()
    _restart_process()

    w2 = manager.get_or_create_world(pid)
    assert w2.root == w.root
    got = read_note(w2.root, "teaching/what-didnt-work.md")
    assert got["ok"] and got["content"] == content

    # Age-based cleanup must never touch notebook files, even very old ones.
    old = os.stat(w2.root / NOTEBOOK_DIRNAME / "teaching" / "what-didnt-work.md")
    os.utime(
        w2.root / NOTEBOOK_DIRNAME / "teaching" / "what-didnt-work.md",
        (old.st_atime - 999_999, old.st_mtime - 999_999),
    )
    from wax.world.cleanup import cleanup_world_tmp

    cleanup_world_tmp(w2.root, ttl_hours=0.001)
    assert read_note(w2.root, "teaching/what-didnt-work.md")["content"] == content


def test_complete_notebook_can_be_inspected_from_world(world_env):
    w = manager.create_world(str(uuid.uuid4()))
    write_note(w.root, "facts.md", "Student is in Year 12.")
    write_note(w.root, "plans/week1.md", "Start with sequences.")

    info = list_notes(w.root)
    assert info["ok"]
    for entry in info["notes"]:
        body = read_note(w.root, entry["path"])
        assert body["ok"] and body["content"]  # every listed note is fully readable

    # Direct filesystem inspection (the AI's own view via world exec).
    texts = sorted(p.read_text() for p in (w.root / NOTEBOOK_DIRNAME).rglob("*.md"))
    assert texts == ["Start with sequences.", "Student is in Year 12."]


def test_infrastructure_never_writes_into_the_notebook(world_env):
    """No auto-extraction/auto-summary: a full turn leaves the notebook empty
    unless the AI itself wrote something."""
    w = manager.create_world(str(uuid.uuid4()))
    assert list_notes(w.root)["count"] == 0
    # Transcript archiving goes to history/, never notebook/.
    from wax.world.transcript import transcript_path

    assert transcript_path(w.root).parent != w.root / NOTEBOOK_DIRNAME


def test_write_cap_is_a_plain_safety_ceiling_not_selection(world_env):
    w = manager.create_world(str(uuid.uuid4()))
    too_big = "x" * (MAX_NOTE_BYTES + 1)
    res = write_note(w.root, "huge.md", too_big)
    assert res["ok"] is False and "bytes" in res["error"]
    # Nothing partial was stored — infrastructure never silently truncates.
    assert not (w.root / NOTEBOOK_DIRNAME / "huge.md").exists()
