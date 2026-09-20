"""Per-principal workspace environment manifest — reproducible, not a full VM.

Records packages/tools the agent installed or detected so the next run
can skip redundant installs and restore intent after container restart.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wax.observability.logging import get_logger
from wax.terminal.workspace import principal_workspace

logger = get_logger(__name__)

MANIFEST_NAME = "environment.json"


def _path(principal_id: Any) -> Path:
    return principal_workspace(principal_id) / MANIFEST_NAME


def load_manifest(principal_id: Any) -> dict[str, Any]:
    p = _path(principal_id)
    if not p.is_file():
        return {
            "version": 1,
            "packages": {},
            "tools": {},
            "updated_at": None,
        }
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("workspace_manifest_corrupt", principal_id=str(principal_id))
        return {"version": 1, "packages": {}, "tools": {}, "updated_at": None}


def save_manifest(principal_id: Any, data: dict[str, Any]) -> dict[str, Any]:
    data = dict(data)
    data["version"] = int(data.get("version") or 1)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    p = _path(principal_id)
    p.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    logger.info(
        "workspace_manifest_saved",
        principal_id=str(principal_id),
        packages=len(data.get("packages") or {}),
    )
    return data


def record_package(
    principal_id: Any,
    name: str,
    *,
    version: str | None = None,
    source: str = "pip",
) -> dict[str, Any]:
    man = load_manifest(principal_id)
    packages = dict(man.get("packages") or {})
    packages[name] = {
        "version": version,
        "source": source,
        "installed_at": datetime.now(timezone.utc).isoformat(),
    }
    man["packages"] = packages
    return save_manifest(principal_id, man)


def has_package(principal_id: Any, name: str) -> bool:
    man = load_manifest(principal_id)
    return name in (man.get("packages") or {})
