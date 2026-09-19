"""
Workspace cleanup — remove old media/tmp files.

Maintenance only. Never blocks tutoring.
"""

from __future__ import annotations

import time
from pathlib import Path

from wax.config import get_settings
from wax.observability.logging import get_logger
from wax.terminal.workspace import workspace_root

logger = get_logger(__name__)
settings = get_settings()


def cleanup_old_files(ttl_hours: int | None = None) -> dict:
    ttl = ttl_hours if ttl_hours is not None else settings.workspace_ttl_hours
    root = workspace_root()
    if not root.exists():
        return {"removed": 0, "bytes": 0}
    cutoff = time.time() - (ttl * 3600)
    removed = 0
    freed = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            if path.stat().st_mtime < cutoff:
                size = path.stat().st_size
                path.unlink(missing_ok=True)
                removed += 1
                freed += size
        except OSError:
            continue
    # prune empty dirs (best effort)
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass
    logger.info("workspace_cleanup", removed=removed, freed_bytes=freed)
    return {"removed": removed, "bytes": freed, "ttl_hours": ttl}
