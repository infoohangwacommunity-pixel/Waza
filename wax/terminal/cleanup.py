"""
State-aware world cleanup — never age-delete runtimes, software, or durable artifacts.

Evict order under pressure: tmp → cache → old history.
"""

from __future__ import annotations

import time
from pathlib import Path

from wax.config import get_settings
from wax.observability.logging import get_logger
from wax.terminal.workspace import workspace_root

logger = get_logger(__name__)
settings = get_settings()

# Paths relative to a world root that must never be TTL-deleted
PROTECTED_PREFIXES = (
    "runtimes/",
    "software/",
    "bin/",
    "artifacts/",
    "workspace/",
    "identity.json",
    "policy.json",
    "lifecycle.json",
    "state/locks/",
)


def _is_protected(rel: str) -> bool:
    rel = rel.replace("\\", "/")
    if rel in ("identity.json", "policy.json", "lifecycle.json"):
        return True
    return any(rel.startswith(p) for p in PROTECTED_PREFIXES)


def cleanup_world_tmp(world_root: Path, ttl_hours: float = 24.0) -> dict:
    cutoff = time.time() - ttl_hours * 3600
    removed = 0
    freed = 0
    for base_name in ("tmp", "cache"):
        base = world_root / base_name
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    sz = path.stat().st_size
                    path.unlink(missing_ok=True)
                    removed += 1
                    freed += sz
            except OSError:
                continue
    return {"removed": removed, "bytes": freed}


def cleanup_old_files(ttl_hours: int | None = None) -> dict:
    """
    Worker entrypoint. Class-aware: only tmp/cache under worlds/; never runtimes/artifacts/workspace.
    Legacy principals/*/tmp still cleaned by age.
    """
    ttl = float(ttl_hours if ttl_hours is not None else getattr(settings, "workspace_ttl_hours", 72) or 72)
    root = workspace_root()
    removed = 0
    freed = 0
    worlds = root / "worlds"
    if worlds.is_dir():
        for wdir in worlds.iterdir():
            if not wdir.is_dir() or wdir.name.startswith("."):
                continue
            r = cleanup_world_tmp(wdir, ttl_hours=min(ttl, 48.0))
            removed += r["removed"]
            freed += r["bytes"]
    # legacy tmp only
    principals = root / "principals"
    if principals.is_dir():
        cutoff = time.time() - ttl * 3600
        for path in principals.rglob("tmp/**/*"):
            if path.is_file():
                try:
                    if path.stat().st_mtime < cutoff:
                        sz = path.stat().st_size
                        path.unlink(missing_ok=True)
                        removed += 1
                        freed += sz
                except OSError:
                    pass
    logger.info("workspace_cleanup", removed=removed, freed_bytes=freed, mode="class_aware")
    return {"removed": removed, "bytes": freed, "ttl_hours": ttl, "mode": "class_aware"}
