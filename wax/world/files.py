"""Safe filesystem operations inside a World."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from wax.world.errors import PathEscape, PolicyDenied
from wax.world.layout import resolve_under_world
from wax.world.manager import World, assert_operable


def list_files(world: World, rel: str = "workspace", *, limit: int = 100) -> dict[str, Any]:
    assert_operable(world, allow_degraded=True)
    base = resolve_under_world(world.root, rel)
    if not base.exists():
        return {"ok": True, "entries": []}
    entries = []
    for p in sorted(base.rglob("*")):
        if p.is_file():
            entries.append(
                {
                    "path": str(p.relative_to(world.root)),
                    "size": p.stat().st_size,
                    "suffix": p.suffix.lower(),
                }
            )
            if len(entries) >= limit:
                break
    return {"ok": True, "entries": entries}


def read_file(world: World, rel: str, *, max_bytes: int = 200_000) -> dict[str, Any]:
    assert_operable(world, allow_degraded=True)
    path = resolve_under_world(world.root, rel)
    if not path.is_file():
        return {"ok": False, "error": "file_not_found", "path": rel}
    data = path.read_bytes()[:max_bytes]
    try:
        text = data.decode("utf-8")
        return {"ok": True, "path": rel, "text": text, "size": len(data)}
    except UnicodeDecodeError:
        return {"ok": True, "path": rel, "size": len(data), "binary": True, "hex_preview": data[:64].hex()}


def write_file(world: World, rel: str, content: str | bytes, *, max_bytes: int = 5_000_000) -> dict[str, Any]:
    assert_operable(world)
    # forbid writing over runtimes/software records casually
    if rel.startswith("runtimes/") or rel.startswith("software/") or rel.startswith("state/locks"):
        raise PolicyDenied("write to protected world path")
    path = resolve_under_world(world.root, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = content.encode("utf-8") if isinstance(content, str) else content
    if len(raw) > max_bytes:
        return {"ok": False, "error": "file_too_large", "size": len(raw)}
    path.write_bytes(raw)
    return {"ok": True, "path": str(path.relative_to(world.root)), "size": len(raw)}


def mkdir(world: World, rel: str) -> dict[str, Any]:
    assert_operable(world)
    path = resolve_under_world(world.root, rel)
    path.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "path": str(path.relative_to(world.root))}


def delete_file(world: World, rel: str) -> dict[str, Any]:
    assert_operable(world)
    if rel.startswith("runtimes/") or rel.startswith("state/"):
        raise PolicyDenied("delete protected path")
    path = resolve_under_world(world.root, rel)
    if path.is_file():
        path.unlink()
        return {"ok": True, "deleted": rel}
    return {"ok": False, "error": "file_not_found"}
