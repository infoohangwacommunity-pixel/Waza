"""Discover — observed reality of a World (not just records)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from wax.world.manager import World
from wax.world.resources import dir_size, world_budget_defaults


def discover(world: World, *, sections: list[str] | None = None) -> dict[str, Any]:
    want = set(sections or ["identity", "lifecycle", "resources", "runtimes", "software", "files", "jobs"])
    out: dict[str, Any] = {"ok": True, "world_id": world.world_id, "principal_id": world.principal_id}
    if "identity" in want:
        out["identity"] = {
            "world_id": world.world_id,
            "principal_id": world.principal_id,
            "schema_version": world.schema_version,
        }
    if "lifecycle" in want:
        out["lifecycle"] = {"state": world.lifecycle, "reason": world.lifecycle_reason}
    if "resources" in want:
        wb = world_budget_defaults()
        used = dir_size(world.root)
        out["resources"] = {
            "disk_used_bytes": used,
            "max_disk_bytes": wb.max_disk_bytes,
            "env_used_bytes": dir_size(world.root / "runtimes") + dir_size(world.root / "bin"),
            "max_env_bytes": wb.max_env_bytes,
            "active_execs": world.active_execs,
            "max_concurrent_execs": wb.max_concurrent_execs,
        }
    if "runtimes" in want:
        out["runtimes"] = _scan_runtimes(world.root)
    if "software" in want:
        out["software"] = _scan_software(world.root)
    if "files" in want:
        out["files"] = {
            "workspace": _list_summary(world.root / "workspace"),
            "artifacts": _list_summary(world.root / "artifacts"),
            "bin": _list_summary(world.root / "bin"),
        }
    if "jobs" in want:
        out["jobs"] = _scan_jobs(world.root)
    out["discovered_at"] = time.time()
    return out


def _scan_runtimes(root: Path) -> list[dict[str, Any]]:
    rt = root / "runtimes"
    found = []
    if not rt.is_dir():
        return found
    for kind in rt.iterdir():
        if not kind.is_dir():
            continue
        for ver in kind.iterdir():
            if not ver.is_dir():
                continue
            py = ver / "bin" / "python"
            py3 = ver / "bin" / "python3"
            healthy = py.is_file() or py3.is_file() or (ver / "bin").is_dir()
            found.append(
                {
                    "kind": kind.name,
                    "name": ver.name,
                    "path": str(ver.relative_to(root)),
                    "healthy": healthy,
                }
            )
    return found


def _scan_software(root: Path) -> list[dict[str, Any]]:
    soft = root / "software"
    items = []
    if not soft.is_dir():
        return items
    for f in sorted(soft.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            items.append(data)
        except Exception:
            items.append({"name": f.stem, "verify_status": "corrupt_record"})
    return items


def _list_summary(path: Path, limit: int = 30) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    try:
        for p in sorted(path.rglob("*")):
            if p.is_file():
                out.append(
                    {
                        "path": str(p.relative_to(path.parent.parent) if path.parent.name != "worlds" else p),
                        "rel": str(p.relative_to(path)),
                        "size": p.stat().st_size,
                    }
                )
                if len(out) >= limit:
                    break
    except Exception:
        pass
    return out


def _scan_jobs(root: Path) -> list[dict[str, Any]]:
    d = root / "state" / "exec"
    if not d.is_dir():
        return []
    jobs = []
    for f in sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
        try:
            jobs.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return jobs
