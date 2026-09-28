"""
World filesystem layout.

world_id is independent of principal_id (owner). Identifiers are never interchangeable.
Single authority for durable workspace roots (no terminal package).
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
settings = get_settings()

SCHEMA_VERSION = 1

DEFAULT_ROOT = os.environ.get("WAX_WORKSPACE_ROOT", "/tmp/wax-workspaces")

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
)


def workspace_root() -> Path:
    """Durable host root for all student Worlds (Railway volume in production)."""
    root = Path(
        getattr(settings, "workspace_root", None)
        or os.environ.get("WAX_WORKSPACE_ROOT")
        or DEFAULT_ROOT
    )
    if settings.app_env == "production" and str(root).startswith("/tmp"):
        logger.error(
            "workspace_root_ephemeral",
            path=str(root),
            hint="Set WORKSPACE_ROOT=/data/wax-workspaces and attach a Railway Volume at /data",
        )
        if getattr(settings, "require_persistent_workspace", False):
            raise RuntimeError(
                "Production workspace must not use /tmp. "
                "Attach a Railway Volume at /data and set WORKSPACE_ROOT=/data/wax-workspaces."
            )
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".wax_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as e:
        if getattr(settings, "require_persistent_workspace", False) or (
            settings.app_env == "production" and str(root).startswith("/data/")
        ):
            raise RuntimeError(
                f"Workspace path {root} is not writable. "
                f"Attach a Railway Volume at /data and set WORKSPACE_ROOT=/data/wax-workspaces. "
                f"Underlying error: {e}"
            ) from e
        logger.warning("workspace_root_not_writable", path=str(root), error=str(e)[:200])
        root = Path("/tmp/wax-workspaces")
        root.mkdir(parents=True, exist_ok=True)
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
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def resolve_under_world(root: Path, rel: str) -> Path:
    """Resolve rel path under world root; raise on escape."""
    from wax.world.errors import PathEscape

    if not rel or rel.startswith("/"):
        candidate = Path(rel).resolve() if rel.startswith("/") else (root / rel).resolve()
    else:
        candidate = (root / rel).resolve()
    root_res = root.resolve()
    try:
        candidate.relative_to(root_res)
    except ValueError as e:
        raise PathEscape(f"path escapes world root: {rel}") from e
    for p in [candidate, *candidate.parents]:
        if p == root_res:
            break
        if p.is_symlink():
            target = p.resolve()
            try:
                target.relative_to(root_res)
            except ValueError as e:
                raise PathEscape(f"symlink escape: {p}") from e
    return candidate


def migrate_legacy_principal(principal_id: str, world_id: str) -> dict[str, Any]:
    """Controlled copy of legacy principals/<id> into world workspace."""
    legacy = workspace_root() / "principals" / str(principal_id).replace("/", "_")[:64]
    root = world_root(world_id)
    ensure_layout(root)
    report: dict[str, Any] = {"legacy": str(legacy), "copied": [], "skipped": []}
    if not legacy.is_dir():
        report["status"] = "no_legacy"
        return report
    mapping = {
        "media": root / "workspace" / "media",
        "out": root / "artifacts",
        "tmp": root / "tmp",
    }
    for name, dest in mapping.items():
        src = legacy / name
        if not src.is_dir():
            report["skipped"].append(name)
            continue
        dest.mkdir(parents=True, exist_ok=True)
        for item in src.iterdir():
            target = dest / item.name
            if target.exists():
                report["skipped"].append(str(item))
                continue
            try:
                if item.is_dir():
                    shutil.copytree(item, target, dirs_exist_ok=False)
                else:
                    shutil.copy2(item, target)
                report["copied"].append(str(item))
            except Exception as e:
                report.setdefault("errors", []).append(f"{item}: {e}")
    report["status"] = "ok"
    logger.info("world_legacy_migrated", world_id=world_id, copied=len(report["copied"]))
    return report


def new_world_id() -> str:
    return str(uuid.uuid4())
