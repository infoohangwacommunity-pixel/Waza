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
    """Resolve the learner workspace via World.

    Does not swallow World security/lifecycle failures into a silent
    legacy path. Legacy principals/<id> is only used when the World
    layout has never been created for this process tree (pre-migration
    hosts) and get_or_create_world is unavailable to import.
    """
    from wax.world.errors import WorldError

    try:
        from wax.world.manager import get_or_create_world

        w = get_or_create_world(str(principal_id))
        path = w.root / "workspace"
        path.mkdir(parents=True, exist_ok=True)
        (path / "media").mkdir(exist_ok=True)
        (path / "out").mkdir(exist_ok=True)
        (path / "projects").mkdir(exist_ok=True)
        return path
    except WorldError:
        raise
    except ImportError:
        pass
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


def path_in_principal_scope(path: str | Path, principal_id: Any, work_id: Any | None = None) -> bool:
    """True if path is under this principal's workspace (or work subdir)."""
    try:
        resolved = Path(path).resolve()
    except Exception:
        return False
    bases: list[Path] = []
    try:
        bases.append(principal_workspace(principal_id).resolve())
    except Exception:
        pass
    if work_id:
        try:
            bases.append(work_workspace(work_id, principal_id).resolve())
        except Exception:
            pass
    for base in bases:
        try:
            resolved.relative_to(base)
            return True
        except ValueError:
            continue
        except Exception:
            continue
    # Same volume root + principal id fragment (worlds/<id>/… layout)
    try:
        root = workspace_root().resolve()
        if str(resolved).startswith(str(root) + os.sep):
            pid = str(principal_id).replace("/", "_")[:64]
            if pid and pid in str(resolved):
                return True
    except Exception:
        pass
    return False


def stage_media_for_work(
    principal_id: Any,
    source_path: str | Path,
    *,
    work_id: Any | None = None,
    filename: str | None = None,
) -> dict[str, Any]:
    """
    Ensure inbound media is readable from the agent workspace.

    Copies (or hardlinks) into principal workspace/media and, when work_id is set,
    into work/<id>/media so terminal cwd can see the file without path_escape.
    """
    src = Path(str(source_path)).resolve()
    if not src.is_file():
        return {"ok": False, "error": "source_missing", "path": str(source_path)}

    base = principal_workspace(principal_id)
    media_dir = base / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    name = filename or src.name
    name = "".join(c for c in name if c.isalnum() or c in "._-")[:120] or f"media-{uuid4().hex[:8]}"
    dest = media_dir / name
    if dest.resolve() != src:
        try:
            if not dest.exists() or dest.stat().st_size != src.stat().st_size:
                dest.write_bytes(src.read_bytes())
        except Exception as e:
            return {"ok": False, "error": f"stage_failed:{e}", "path": str(src)}
    else:
        dest = src

    work_path = None
    if work_id:
        wmedia = work_workspace(work_id, principal_id) / "media"
        wmedia.mkdir(parents=True, exist_ok=True)
        wdest = wmedia / name
        try:
            if not wdest.exists() or wdest.stat().st_size != dest.stat().st_size:
                wdest.write_bytes(dest.read_bytes())
            work_path = str(wdest.resolve())
        except Exception as e:
            logger.warning("work_media_stage_failed", error=str(e)[:200])

    return {
        "ok": True,
        "path": str(dest.resolve()),
        "work_path": work_path,
        "principal_media_dir": str(media_dir.resolve()),
        "filename": name,
        "size": dest.stat().st_size if dest.exists() else 0,
    }
