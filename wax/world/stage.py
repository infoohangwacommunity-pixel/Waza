"""Stage inbound media and files into a student's World."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from wax.config import get_settings
from wax.observability.logging import get_logger
from wax.world.manager import get_or_create_world

logger = get_logger(__name__)
settings = get_settings()


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_write_bytes(
    dest_dir: Path, filename: str, data: bytes, max_bytes: int | None = None
) -> Path:
    max_b = max_bytes or int(getattr(settings, "workspace_max_file_bytes", 25_000_000) or 25_000_000)
    dest_dir.mkdir(parents=True, exist_ok=True)
    if len(data) > max_b:
        raise ValueError(f"file_too_large:{len(data)}")
    name = "".join(c for c in filename if c.isalnum() or c in "._-")[:120] or f"file-{uuid4().hex[:8]}"
    dest = dest_dir / name
    if dest.exists():
        dest = dest_dir / f"{dest.stem}-{uuid4().hex[:6]}{dest.suffix}"
    dest.write_bytes(data)
    return dest


def principal_workspace(principal_id: str | Any) -> Path:
    """Student working directory inside their World."""
    w = get_or_create_world(str(principal_id))
    path = w.root / "workspace"
    path.mkdir(parents=True, exist_ok=True)
    (path / "media").mkdir(exist_ok=True)
    (path / "projects").mkdir(exist_ok=True)
    return path


def stage_media_for_work(
    principal_id: str | Any,
    source_path: str | Path,
    *,
    work_id: Any = None,
    filename: str | None = None,
) -> dict[str, Any]:
    """
    Copy an inbound media file into the student's World media area.
    AI decides how to process it later — infrastructure only places the file.
    """
    src = Path(source_path)
    if not src.is_file():
        return {"ok": False, "error": "source_not_found", "path": str(source_path)}
    try:
        ws = principal_workspace(principal_id)
        media = ws / "media"
        media.mkdir(parents=True, exist_ok=True)
        name = filename or src.name
        name = "".join(c for c in name if c.isalnum() or c in "._-")[:120] or f"media-{uuid4().hex[:8]}"
        dest = media / name
        if dest.exists():
            dest = media / f"{dest.stem}-{uuid4().hex[:6]}{dest.suffix}"
        shutil.copy2(src, dest)
        work_path = None
        if work_id:
            work_dir = ws / "work" / str(work_id)[:36] / "media"
            work_dir.mkdir(parents=True, exist_ok=True)
            work_dest = work_dir / dest.name
            if not work_dest.exists():
                try:
                    work_dest.hardlink_to(dest)
                except OSError:
                    shutil.copy2(dest, work_dest)
            work_path = str(work_dest)
        return {
            "ok": True,
            "path": str(dest),
            "work_path": work_path,
            "filename": dest.name,
            "size": dest.stat().st_size,
        }
    except Exception as e:
        logger.exception("stage_media_failed", principal_id=str(principal_id))
        return {"ok": False, "error": str(e)[:500]}
