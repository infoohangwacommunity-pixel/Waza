"""
AI workspace — durable file space the tutor can use via the terminal.

Media (images, audio, video, documents) is downloaded here once.
The model receives paths and can inspect/extract with tools instead of
shipping every binary through expensive multimodal API calls by default.

Infrastructure limits remain: isolation, size caps, allowed tools.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from wax.config import get_settings
from wax.observability.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

# Root for all workspaces (not the app secrets tree)
DEFAULT_ROOT = os.environ.get("WAX_WORKSPACE_ROOT", "/tmp/wax-workspaces")


def workspace_root() -> Path:
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
            settings.app_env == "production"
            and str(root).startswith("/data/")
        ):
            raise RuntimeError(
                f"Workspace path {root} is not writable. "
                f"Attach a Railway Volume at /data and set WORKSPACE_ROOT=/data/wax-workspaces. "
                f"Underlying error: {e}"
            ) from e
        logger.warning("workspace_root_not_writable", path=str(root), error=str(e)[:200])
        # fall back to /tmp for non-strict envs
        root = Path("/tmp/wax-workspaces")
        root.mkdir(parents=True, exist_ok=True)
    return root


def principal_workspace(principal_id: str | Any) -> Path:
    """
    Compatibility path for media/tools.

    Prefer the World workspace when a world exists for this principal;
    otherwise fall back to legacy principals/<id> layout (no permanent symlinks).
    """
    safe = str(principal_id).replace("/", "_")[:64]
    try:
        from wax.world.manager import get_or_create_world

        w = get_or_create_world(str(principal_id))
        path = w.root / "workspace"
        path.mkdir(parents=True, exist_ok=True)
        (path / "media").mkdir(exist_ok=True)
        (path / "projects").mkdir(exist_ok=True)
        return path
    except Exception:
        pass
    path = workspace_root() / "principals" / safe
    path.mkdir(parents=True, exist_ok=True)
    (path / "media").mkdir(exist_ok=True)
    (path / "out").mkdir(exist_ok=True)
    (path / "tmp").mkdir(exist_ok=True)
    return path


def work_workspace(work_id: str | Any, principal_id: str | Any | None = None) -> Path:
    if principal_id:
        base = principal_workspace(principal_id) / "work" / str(work_id)[:36]
    else:
        base = workspace_root() / "work" / str(work_id)[:36]
    base.mkdir(parents=True, exist_ok=True)
    (base / "media").mkdir(exist_ok=True)
    (base / "out").mkdir(exist_ok=True)
    return base


def list_files(path: Path, limit: int = 50) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    items = []
    for p in sorted(path.rglob("*")):
        if p.is_file():
            items.append(
                {
                    "path": str(p),
                    "rel": str(p.relative_to(path)) if path in p.parents or p.parent == path else p.name,
                    "size": p.stat().st_size,
                    "suffix": p.suffix.lower(),
                }
            )
            if len(items) >= limit:
                break
    return items


def safe_write_bytes(dest_dir: Path, filename: str, data: bytes, max_bytes: int = 25_000_000) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    if len(data) > max_bytes:
        raise ValueError(f"file_too_large:{len(data)}")
    # sanitize name
    name = "".join(c for c in filename if c.isalnum() or c in "._-")[:120] or f"file-{uuid4().hex[:8]}"
    dest = dest_dir / name
    if dest.exists():
        dest = dest_dir / f"{dest.stem}-{uuid4().hex[:6]}{dest.suffix}"
    dest.write_bytes(data)
    return dest


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
