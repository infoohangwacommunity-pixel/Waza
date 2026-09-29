"""AI-owned notebook — durable files inside each student's World.

The notebook is *only* reality: a protected directory (``notebook/``) in the
student's own World that the AI can create, read, modify and organize through
its existing ``world`` capabilities (exec cwd / absolute paths under its root).

What this module deliberately does NOT do (intelligence belongs to the AI):
- no fixed schema for what the AI must remember;
- no automatic extraction of memories from messages;
- no automatic summarization of conversations;
- no importance ranking, embedding, classification or relevance selection;
- no discarding of older notes because they are old.

Infrastructure guarantees exactly three things: persistence (durable volume,
never age-scavenged), isolation (one notebook per principal, path-escape safe)
and safe file access (atomic writes, size-capped reads as transport limits).

Suggested layout lives in ``NOTEBOOK_HINT`` and is shown to the AI verbatim —
it is a hint the AI may ignore, not an enforced structure.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from wax.observability.logging import get_logger
from wax.world import layout
from wax.world.errors import PathEscape

logger = get_logger(__name__)

NOTEBOOK_DIRNAME = "notebook"

# Plain transport ceilings only — not semantic compression, not selection.
# A note larger than these is the AI's problem to reorganize; infrastructure
# never silently truncates content on write.
MAX_NOTE_BYTES = 256 * 1024          # per-file write cap (safety, not meaning)
READ_CAP_BYTES = 64 * 1024           # default bounded read for observations
TOTAL_READ_CAP_BYTES = 256 * 1024    # whole-notebook listing safety cap

NOTEBOOK_HINT = (
    "You have a durable notebook/ folder in your World — long-term notes you "
    "own completely. Create, read, edit and organize files there however you "
    "judge useful (for example: goals, important facts, preferences, learning "
    "history, topics discussed, explanations that worked or did not work, "
    "plans, observations about the student). There is no required schema. "
    "Nothing is written or organized for you automatically, and nothing is "
    "deleted because it is old. Keep it readable, but you decide what deserves "
    "a note."
)


def notebook_dir(world_root: Path) -> Path:
    """The notebook area of one World (created on demand, idempotent)."""
    nb = world_root / NOTEBOOK_DIRNAME
    nb.mkdir(parents=True, exist_ok=True)
    return nb


def _resolve(world_root: Path, rel: str) -> Path:
    """Safe access: resolve rel strictly under this World's notebook."""
    root_res = world_root.resolve()
    candidate = layout.resolve_under_world(root_res, rel)
    nb_res = (root_res / NOTEBOOK_DIRNAME).resolve()
    try:
        candidate.relative_to(nb_res)
    except ValueError as e:
        raise PathEscape(f"path escapes notebook area: {rel}") from e
    return candidate


def write_note(world_root: Path, rel_path: str, content: str) -> dict[str, Any]:
    """Atomically write one note file inside the notebook.

    Creates parent directories as needed (the AI organizes its own structure).
    Idempotent: rewriting the same path replaces the previous bytes fully.
    """
    if len(content.encode("utf-8")) > MAX_NOTE_BYTES:
        return {"ok": False, "error": f"note_exceeds_{MAX_NOTE_BYTES}_bytes"}
    target = _resolve(world_root, f"{NOTEBOOK_DIRNAME}/{rel_path.lstrip('/')}")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, target)
    except OSError:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
    return {"ok": True, "path": str(target.relative_to(world_root)), "bytes": len(content.encode("utf-8"))}


def read_note(world_root: Path, rel_path: str, *, limit: int = READ_CAP_BYTES) -> dict[str, Any]:
    """Read one note (bounded by a plain transport cap, never transformed)."""
    target = _resolve(world_root, f"{NOTEBOOK_DIRNAME}/{rel_path.lstrip('/')}")
    if not target.is_file():
        return {"ok": False, "error": "not_found", "path": rel_path}
    data = target.read_bytes()[:limit]
    return {
        "ok": True,
        "path": str(target.relative_to(world_root)),
        "content": data.decode("utf-8", errors="replace"),
        "truncated_by_transport_cap": len(data) >= limit and target.stat().st_size > limit,
    }


def list_notes(world_root: Path) -> dict[str, Any]:
    """Return every note path + size in order. No ranking, no filtering."""
    nb = notebook_dir(world_root)
    entries = []
    total = 0
    for p in sorted(nb.rglob("*")):
        if not p.is_file():
            continue
        try:
            sz = p.stat().st_size
        except OSError:
            continue
        total += sz
        if total > TOTAL_READ_CAP_BYTES:
            entries.append({"path": "...", "bytes": 0, "listing_capped": True})
            break
        entries.append(
            {
                "path": str(p.relative_to(nb)),
                "bytes": sz,
                "mtime": p.stat().st_mtime,
            }
        )
    return {"ok": True, "notebook": str(nb), "count": len(entries), "notes": entries}


def world_root_for_principal(principal_id: Any) -> Path | None:
    """Locate a principal's World root via the canonical principal index.

    Never creates a World here — creating belongs to the normal turn flow
    (world ops / manager.get_or_create_world). Returns None when unknown.
    """
    if not principal_id:
        return None
    from wax.world import manager

    pid = str(principal_id)
    idx = layout.read_json(
        layout.worlds_root() / ".principal_index" / f"{manager._safe(pid)}.json"
    )
    if not idx or not idx.get("world_id"):
        return None
    root = layout.world_root(str(idx["world_id"]))
    return root if root.is_dir() else None
