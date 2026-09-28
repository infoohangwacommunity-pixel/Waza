"""
Temporary-file cleanup only.

A student's durable World (workspace, projects, software, runtimes, history)
MUST NOT be age-deleted. Worker restarts and deployments must not erase Worlds
when WORKSPACE_ROOT points at a durable volume.

TTL settings apply only to tmp/ and cache/ trees inside a world.
"""

from __future__ import annotations

import time
from pathlib import Path

from wax.config import get_settings
from wax.observability.logging import get_logger
from wax.world.layout import workspace_root

logger = get_logger(__name__)
settings = get_settings()

# Paths that are durable student state — never age-delete these trees.
_DURABLE_TOP = frozenset(
    {
        "workspace",
        "projects",
        "software",
        "runtimes",
        "history",
        "bin",
        "state",  # locks/lifecycle; not bulk-deleted by age
    }
)

# Only these may be age-scavenged
_TEMP_TOP = frozenset({"tmp", "cache"})


def cleanup_world_tmp(world_root: Path, ttl_hours: float = 48.0) -> dict:
    """Remove aged files under world tmp/ and cache/ only."""
    cutoff = time.time() - max(1.0, float(ttl_hours)) * 3600
    removed = 0
    freed = 0
    for base_name in _TEMP_TOP:
        base = world_root / base_name
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            # refuse if resolved path escapes temp tree (paranoia)
            try:
                path.resolve().relative_to(base.resolve())
            except ValueError:
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
    Scavenge temporary files across all worlds.

    Does NOT delete world directories.
    Does NOT delete workspace/, projects/, software/, runtimes/, or history/.
    """
    ttl = ttl_hours
    if ttl is None:
        ttl = getattr(settings, "workspace_tmp_ttl_hours", None)
    if ttl is None:
        ttl = getattr(settings, "workspace_ttl_hours", 48)
    ttl = float(ttl or 48)

    root = workspace_root()
    removed = 0
    freed = 0
    worlds = root / "worlds"
    if worlds.is_dir():
        for wdir in worlds.iterdir():
            if not wdir.is_dir() or wdir.name.startswith("."):
                continue
            # Never remove the world directory itself based on age
            r = cleanup_world_tmp(wdir, ttl_hours=ttl)
            removed += r["removed"]
            freed += r["bytes"]

    # Legacy principals/ tree — tmp only
    principals = root / "principals"
    if principals.is_dir():
        cutoff = time.time() - ttl * 3600
        for path in principals.rglob("*"):
            if not path.is_file():
                continue
            # only under .../tmp/...
            parts = path.parts
            if "tmp" not in parts and "cache" not in parts:
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    sz = path.stat().st_size
                    path.unlink(missing_ok=True)
                    removed += 1
                    freed += sz
            except OSError:
                pass

    logger.info(
        "workspace_tmp_cleanup",
        removed=removed,
        freed_bytes=freed,
        tmp_ttl_hours=ttl,
        durable_preserved=True,
    )
    return {
        "removed": removed,
        "bytes": freed,
        "tmp_ttl_hours": ttl,
        "mode": "tmp_cache_only",
        "durable_preserved": True,
    }
