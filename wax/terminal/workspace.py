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
    root = Path(getattr(settings, "workspace_root", None) or DEFAULT_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    return root


def principal_workspace(principal_id: str | Any) -> Path:
    safe = str(principal_id).replace("/", "_")[:64]
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
