"""
Lightweight durable media asset + evidence registry (principal-scoped files).

Not a full MediaGroup / CMS. Stores JSON manifests under the principal workspace
so probes and processor results can be reloaded within a work session and across
restarts without stuffing everything only into Work.input_payload.

Schema is intentionally small and extensible.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from wax.media.types import ExtractionEvidence, MediaProbe
from wax.terminal.workspace import principal_workspace


def _assets_dir(principal_id: Any) -> Path:
    d = principal_workspace(principal_id) / "media" / "assets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_id(value: str) -> str:
    return "".join(c for c in value if c.isalnum() or c in "-_")[:64]


def register_asset(
    principal_id: Any,
    *,
    path: str,
    probe: MediaProbe | dict[str, Any] | None = None,
    work_id: str | None = None,
    channel: str | None = None,
    media_id: str | None = None,
) -> dict[str, Any]:
    """Register a local media file as an asset; returns asset manifest."""
    asset_id = _safe_id(media_id or "") or uuid.uuid4().hex[:16]
    base = _assets_dir(principal_id)
    manifest_path = base / f"{asset_id}.json"
    probe_dict = probe.to_dict() if isinstance(probe, MediaProbe) else (probe or {})
    manifest = {
        "asset_id": asset_id,
        "path": path,
        "probe": probe_dict,
        "work_id": work_id,
        "channel": channel,
        "media_id": media_id,
        "created_at": time.time(),
        "evidence": [],
    }
    if manifest_path.is_file():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            existing.update({k: v for k, v in manifest.items() if k != "evidence"})
            existing.setdefault("evidence", [])
            manifest = existing
        except Exception:
            pass
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def append_evidence(
    principal_id: Any,
    asset_id: str,
    evidence: ExtractionEvidence | dict[str, Any],
) -> dict[str, Any] | None:
    base = _assets_dir(principal_id)
    manifest_path = base / f"{_safe_id(asset_id)}.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    ev = evidence.to_dict() if isinstance(evidence, ExtractionEvidence) else dict(evidence)
    ev.setdefault("created_at", time.time())
    manifest.setdefault("evidence", []).append(ev)
    # cap history per asset
    if len(manifest["evidence"]) > 40:
        manifest["evidence"] = manifest["evidence"][-40:]
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def load_asset(principal_id: Any, asset_id: str) -> dict[str, Any] | None:
    path = _assets_dir(principal_id) / f"{_safe_id(asset_id)}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_assets(principal_id: Any, *, limit: int = 50) -> list[dict[str, Any]]:
    base = _assets_dir(principal_id)
    out: list[dict[str, Any]] = []
    for f in sorted(base.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out
