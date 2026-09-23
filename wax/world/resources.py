"""Three-layer resource governance: execution + world + worker."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any

from wax.config import get_settings
from wax.world.errors import ResourceDenied, QuotaExceeded

settings = get_settings()

# Worker-level in-process counters (single worker process assumption; multi-worker uses softer limits)
_lock = threading.Lock()
_worker_active_execs = 0
_worker_active_pids_est = 0


@dataclass
class ExecutionBudget:
    wall_sec: float = 60.0
    memory_bytes: int = 512 * 1024 * 1024
    cpu_seconds: int = 30
    pids: int = 64
    max_output: int = 150_000
    network_mode: str = "none"


@dataclass
class WorldBudget:
    max_disk_bytes: int = 2 * 1024 * 1024 * 1024  # 2 GiB default
    max_env_bytes: int = 1024 * 1024 * 1024
    max_cache_bytes: int = 512 * 1024 * 1024
    max_concurrent_execs: int = 2


@dataclass
class WorkerBudget:
    max_concurrent_execs: int = 8
    max_pids_est: int = 256
    disk_floor_bytes: int = 512 * 1024 * 1024


def interactive_budget() -> ExecutionBudget:
    return ExecutionBudget(
        wall_sec=float(getattr(settings, "terminal_timeout_seconds", 60) or 60),
        memory_bytes=int(getattr(settings, "terminal_memory_bytes", 512 * 1024 * 1024)),
        cpu_seconds=int(getattr(settings, "terminal_cpu_seconds", 30) or 30),
        pids=64,
        max_output=int(getattr(settings, "terminal_max_output_bytes", 150_000)),
        network_mode="none",
    )


def batch_budget() -> ExecutionBudget:
    b = interactive_budget()
    b.wall_sec = min(1800.0, b.wall_sec * 20)
    b.cpu_seconds = min(600, b.cpu_seconds * 10)
    return b


def acquire_budget() -> ExecutionBudget:
    b = interactive_budget()
    b.wall_sec = 300.0
    b.network_mode = "pkg"
    b.memory_bytes = max(b.memory_bytes, 768 * 1024 * 1024)
    return b


def world_budget_defaults() -> WorldBudget:
    return WorldBudget()


def worker_budget_defaults() -> WorkerBudget:
    return WorkerBudget()


def dir_size(path) -> int:
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    except OSError:
        pass
    return total


def admit_execution(
    *,
    world_root,
    world_concurrent: int,
    budget: ExecutionBudget,
    world_bud: WorldBudget | None = None,
    worker_bud: WorkerBudget | None = None,
) -> None:
    wb = world_bud or world_budget_defaults()
    wkb = worker_bud or worker_budget_defaults()
    if world_concurrent >= wb.max_concurrent_execs:
        raise ResourceDenied("world concurrent execution limit", detail=str(wb.max_concurrent_execs))
    global _worker_active_execs, _worker_active_pids_est
    with _lock:
        if _worker_active_execs >= wkb.max_concurrent_execs:
            raise ResourceDenied("worker concurrent execution limit")
        if _worker_active_pids_est + budget.pids > wkb.max_pids_est:
            raise ResourceDenied("worker PID budget exhausted")
        _worker_active_execs += 1
        _worker_active_pids_est += budget.pids


def release_execution(budget: ExecutionBudget) -> None:
    global _worker_active_execs, _worker_active_pids_est
    with _lock:
        _worker_active_execs = max(0, _worker_active_execs - 1)
        _worker_active_pids_est = max(0, _worker_active_pids_est - budget.pids)


def check_world_disk(world_root, world_bud: WorldBudget | None = None) -> dict[str, Any]:
    wb = world_bud or world_budget_defaults()
    used = dir_size(world_root)
    env_used = dir_size(world_root / "runtimes") + dir_size(world_root / "bin")
    if used > wb.max_disk_bytes:
        raise QuotaExceeded("world disk quota exceeded", detail=str(used))
    if env_used > wb.max_env_bytes:
        raise QuotaExceeded("world env size quota exceeded", detail=str(env_used))
    return {"disk_used": used, "env_used": env_used, "max_disk": wb.max_disk_bytes}
