"""World Manager — create/load worlds, lifecycle, ownership."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from wax.observability.logging import get_logger
from wax.world import layout
from wax.world.errors import WorldNotReady, WorldDegraded, QuotaExceeded
from wax.world.resources import check_world_disk, world_budget_defaults

logger = get_logger(__name__)

Lifecycle = Literal[
    "CREATING",
    "READY",
    "BUSY",
    "DEGRADED",
    "RECOVERING",
    "QUOTA_EXCEEDED",
    "SUSPENDED",
    "ARCHIVED",
]


@dataclass
class World:
    world_id: str
    principal_id: str
    root: Path
    lifecycle: Lifecycle = "READY"
    lifecycle_reason: str = ""
    schema_version: int = layout.SCHEMA_VERSION
    # in-process concurrent exec count for this world
    active_execs: int = 0

    def identity_path(self) -> Path:
        return self.root / "identity.json"

    def lifecycle_path(self) -> Path:
        return self.root / "lifecycle.json"

    def policy_path(self) -> Path:
        return self.root / "policy.json"

    def to_public(self) -> dict[str, Any]:
        return {
            "world_id": self.world_id,
            "principal_id": self.principal_id,
            "lifecycle": self.lifecycle,
            "lifecycle_reason": self.lifecycle_reason,
            "root": str(self.root),
            "schema_version": self.schema_version,
        }


# principal_id -> World (process cache)
_cache: dict[str, World] = {}


def _default_policy() -> dict[str, Any]:
    wb = world_budget_defaults()
    return {
        "max_disk_bytes": wb.max_disk_bytes,
        "max_env_bytes": wb.max_env_bytes,
        "max_cache_bytes": wb.max_cache_bytes,
        "max_concurrent_execs": wb.max_concurrent_execs,
        "default_network_mode": "none",
    }


def create_world(principal_id: str, *, world_id: str | None = None, migrate_legacy: bool = True) -> World:
    """Create a new world with independent world_id; principal_id is owner only."""
    wid = world_id or layout.new_world_id()
    root = layout.world_root(wid)
    layout.ensure_layout(root)
    now = time.time()
    identity = {
        "world_id": wid,
        "principal_id": str(principal_id),
        "created_at": now,
        "schema_version": layout.SCHEMA_VERSION,
    }
    layout.write_json(root / "identity.json", identity)
    layout.write_json(
        root / "lifecycle.json",
        {"state": "READY", "reason": "", "updated_at": now},
    )
    layout.write_json(root / "policy.json", _default_policy())
    if migrate_legacy:
        try:
            layout.migrate_legacy_principal(str(principal_id), wid)
        except Exception:
            logger.exception("world_legacy_migrate_failed", principal_id=str(principal_id))
    # index by principal for v1 lookup
    index = layout.worlds_root() / ".principal_index"
    index.mkdir(exist_ok=True)
    layout.write_json(index / f"{_safe(principal_id)}.json", {"world_id": wid, "principal_id": str(principal_id)})
    w = World(world_id=wid, principal_id=str(principal_id), root=root, lifecycle="READY")
    _cache[str(principal_id)] = w
    logger.info("world_created", world_id=wid, principal_id=str(principal_id))
    return w


def _safe(pid: str) -> str:
    return "".join(c for c in str(pid) if c.isalnum() or c in "-_")[:64]


def _load_from_disk(world_id: str) -> World | None:
    root = layout.world_root(world_id)
    ident = layout.read_json(root / "identity.json")
    if not ident:
        return None
    life = layout.read_json(root / "lifecycle.json") or {}
    return World(
        world_id=str(ident["world_id"]),
        principal_id=str(ident["principal_id"]),
        root=root,
        lifecycle=life.get("state") or "READY",  # type: ignore[arg-type]
        lifecycle_reason=life.get("reason") or "",
        schema_version=int(ident.get("schema_version") or 1),
    )


def get_or_create_world(principal_id: str) -> World:
    pid = str(principal_id)
    if pid in _cache:
        return _cache[pid]
    # principal index
    idx = layout.read_json(layout.worlds_root() / ".principal_index" / f"{_safe(pid)}.json")
    if idx and idx.get("world_id"):
        w = _load_from_disk(str(idx["world_id"]))
        if w:
            _cache[pid] = w
            return w
    return create_world(pid)


def set_lifecycle(world: World, state: Lifecycle, reason: str = "") -> None:
    world.lifecycle = state
    world.lifecycle_reason = reason
    layout.write_json(
        world.lifecycle_path(),
        {"state": state, "reason": reason, "updated_at": time.time()},
    )
    logger.info("world_lifecycle_changed", world_id=world.world_id, state=state, reason=reason)


def assert_operable(world: World, *, allow_degraded: bool = False) -> None:
    if world.lifecycle in ("SUSPENDED", "ARCHIVED", "CREATING"):
        raise WorldNotReady(f"world state={world.lifecycle}", detail=world.lifecycle_reason)
    if world.lifecycle == "QUOTA_EXCEEDED":
        raise QuotaExceeded("world quota exceeded", detail=world.lifecycle_reason)
    if world.lifecycle in ("DEGRADED", "RECOVERING") and not allow_degraded:
        raise WorldDegraded(world.lifecycle_reason or "world degraded")


def refresh_quota_state(world: World) -> dict[str, Any]:
    try:
        stats = check_world_disk(world.root)
        if world.lifecycle == "QUOTA_EXCEEDED":
            set_lifecycle(world, "READY", "quota recovered")
        return stats
    except QuotaExceeded as e:
        set_lifecycle(world, "QUOTA_EXCEEDED", e.message)
        raise
