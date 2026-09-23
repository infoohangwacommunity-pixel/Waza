"""Deterministic World recovery after worker death / container replace."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from wax.observability.logging import get_logger
from wax.world import layout
from wax.world.manager import set_lifecycle, _load_from_disk

logger = get_logger(__name__)

STALE_EXEC_SEC = 90.0
STALE_LOCK_SEC = 600.0


def reconcile_world_root(root: Path) -> dict[str, Any]:
    report: dict[str, Any] = {"root": str(root), "exec_closed": 0, "locks_cleared": 0}
    ident = layout.read_json(root / "identity.json")
    if not ident:
        report["status"] = "no_identity"
        return report
    world = _load_from_disk(str(ident.get("world_id")))
    if world is None:
        report["status"] = "unloadable"
        return report

    now = time.time()
    exec_dir = root / "state" / "exec"
    running = 0
    if exec_dir.is_dir():
        for f in exec_dir.glob("*.json"):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if rec.get("status") != "running":
                continue
            started = float(rec.get("started_at") or 0)
            if started and now - started < STALE_EXEC_SEC:
                running += 1
                continue
            rec["status"] = "interrupted"
            rec["error"] = "reconciled_after_worker_restart"
            rec["finished_at"] = now
            f.write_text(json.dumps(rec), encoding="utf-8")
            report["exec_closed"] += 1

    lock_dir = root / "state" / "locks"
    if lock_dir.is_dir():
        for f in lock_dir.glob("*"):
            if not f.is_file():
                continue
            try:
                if now - f.stat().st_mtime > STALE_LOCK_SEC:
                    f.unlink(missing_ok=True)
                    report["locks_cleared"] += 1
            except OSError:
                pass

    installs = root / "state" / "installs"
    if installs.is_dir():
        for f in installs.glob("*.json"):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if rec.get("status") == "running":
                rec["status"] = "interrupted"
                rec["error"] = "reconciled_after_worker_restart"
                f.write_text(json.dumps(rec), encoding="utf-8")
                report.setdefault("installs_interrupted", 0)
                report["installs_interrupted"] += 1

    if world.lifecycle in ("BUSY", "RECOVERING") and running == 0:
        set_lifecycle(world, "READY", "reconciled after worker restart")
        report["lifecycle"] = "READY"
    elif world.lifecycle == "BUSY" and running:
        report["lifecycle"] = "BUSY"
    report["status"] = "ok"
    logger.info("world_reconciled", world_id=world.world_id, **{k: v for k, v in report.items() if k != "root"})
    return report


def reconcile_all_worlds() -> dict[str, Any]:
    root = layout.worlds_root()
    out = {"worlds": 0, "reports": []}
    if not root.is_dir():
        return out
    for child in root.iterdir():
        if not child.is_dir() or child.name.startswith("."):
            continue
        if not (child / "identity.json").is_file():
            continue
        out["worlds"] += 1
        out["reports"].append(reconcile_world_root(child))
    return out
