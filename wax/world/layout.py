"""
World filesystem layout.

world_id is independent of principal_id (owner).

Student Worlds are durable filesystem trees under WORKSPACE_ROOT:
  {WORKSPACE_ROOT}/worlds/{world_id}/...

On Railway, a Volume attaches to **one** service only. Web and Worker cannot
share the same block volume. The authoritative Student World store therefore
lives on the **Worker** volume (Worker creates, executes, stages media, and
archives transcript). Postgres remains authoritative for identity, Work,
messages, and memory. Artifacts that must be read by Web use the artifact
storage backend (prefer S3 in multi-service deploys).

Configuration (single rule):
  WORKSPACE_ROOT          canonical path (e.g. /data/wax-workspaces)
  WAX_WORKSPACE_ROOT      accepted alias
  REQUIRE_PERSISTENT_WORKSPACE=true  forbids /tmp and silent fallback

WAX_SHARED_STORE_PATH is treated as an alias of WORKSPACE_ROOT only — it does
not create a network shared filesystem. Railway does not provide multi-service
volume mounts.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from wax.config import get_settings
from wax.observability.logging import get_logger

logger = get_logger(__name__)

SCHEMA_VERSION = 1

SUBDIRS = (
    "workspace",
    "workspace/media",
    "workspace/projects",
    "runtimes",
    "software",
    "bin",
    "artifacts",
    "cache",
    "tmp",
    "state",
    "state/locks",
    "state/installs",
    "state/exec",
    "history",
    "notebook",
)


def _configured_root_string() -> str:
    """Single deterministic resolution of the configured World store path."""
    s = get_settings()
    # Prefer explicit env so deploy vars always win over baked defaults
    for key in (
        "WORKSPACE_ROOT",
        "WAX_WORKSPACE_ROOT",
        "WAX_SHARED_STORE_PATH",  # legacy alias only
    ):
        v = (os.environ.get(key) or "").strip()
        if v:
            return v
    # Settings fields (pydantic also loads WORKSPACE_ROOT → workspace_root)
    for attr in ("workspace_root", "shared_store_root"):
        v = (getattr(s, attr, None) or "").strip()
        if v:
            return v
    return ""


def workspace_root() -> Path:
    """
    Durable host root for all student Worlds.

    Production: must be a non-/tmp path on a volume attached to this process
    (the Worker). Never silently falls back to /tmp when persistence is required.
    """
    s = get_settings()
    configured = _configured_root_string()
    if not configured:
        if s.app_env == "production" or getattr(s, "require_persistent_workspace", False):
            raise RuntimeError(
                "WORKSPACE_ROOT is not set. On Railway, attach a Volume to the "
                "Worker at /data and set WORKSPACE_ROOT=/data/wax-workspaces "
                "with REQUIRE_PERSISTENT_WORKSPACE=true."
            )
        configured = "/tmp/wax-workspaces"
        logger.warning(
            "workspace_root_default_tmp",
            path=configured,
            hint="Development only. Production must set WORKSPACE_ROOT on a volume.",
        )

    root = Path(configured)

    if str(root).startswith("/tmp"):
        logger.error(
            "workspace_root_ephemeral",
            path=str(root),
            hint=(
                "Student Worlds must not live under /tmp. "
                "Attach a Railway Volume to the Worker at /data and set "
                "WORKSPACE_ROOT=/data/wax-workspaces."
            ),
        )
        if s.app_env == "production" or getattr(s, "require_persistent_workspace", False):
            raise RuntimeError(
                f"Refusing ephemeral WORKSPACE_ROOT={root}. "
                "Attach a Railway Volume to the Worker and set "
                "WORKSPACE_ROOT=/data/wax-workspaces with "
                "REQUIRE_PERSISTENT_WORKSPACE=true."
            )

    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".wax_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as e:
        msg = (
            f"Workspace path {root} is not writable ({e}). "
            "On Railway the Volume must be attached to **this** service "
            "(Worker owns Student Worlds) at the mount path that matches "
            "WORKSPACE_ROOT (e.g. volume mount /data → WORKSPACE_ROOT=/data/wax-workspaces)."
        )
        if s.app_env == "production" or getattr(s, "require_persistent_workspace", False):
            raise RuntimeError(msg) from e
        logger.warning("workspace_root_not_writable", path=str(root), error=str(e)[:200])
        # Dev-only escape: never used when persistence is required
        root = Path("/tmp/wax-workspaces")
        root.mkdir(parents=True, exist_ok=True)
        logger.warning("workspace_root_dev_fallback", path=str(root))

    return root


def worlds_root() -> Path:
    root = workspace_root() / "worlds"
    root.mkdir(parents=True, exist_ok=True)
    return root


def world_root(world_id: str) -> Path:
    safe = "".join(c for c in str(world_id) if c.isalnum() or c in "-_")[:64]
    return worlds_root() / safe


def ensure_layout(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for sub in SUBDIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def new_world_id() -> str:
    return uuid.uuid4().hex


def resolve_under_world(world_root_path: Path, rel: str) -> Path:
    """Resolve a relative path under a world root without escaping."""
    safe = (rel or "").replace("..", "").lstrip("/")
    full = (world_root_path / safe).resolve()
    root_resolved = world_root_path.resolve()
    if not str(full).startswith(str(root_resolved)):
        raise ValueError("path_escape")
    return full


def prior_principal_path(principal_id: str) -> Path:
    """Legacy principal-keyed path (migration aid only)."""
    return workspace_root() / "principals" / str(principal_id).replace("/", "_")[:64]


def remove_tree(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
