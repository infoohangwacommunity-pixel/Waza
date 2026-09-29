"""
Infrastructure safety limits for World execution.

Every limit below protects the host and other students. None of them encode
teaching policy or what the AI is conceptually allowed to accomplish.

There is one execution profile. Package install may enable a constrained
network mode ("pkg") because pip/apt need outbound package mirrors — that is
a security exception, not a separate intelligence class.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any

from wax.config import get_settings
from wax.world.errors import ResourceDenied, QuotaExceeded

settings = get_settings()

_lock = threading.Lock()
_worker_active_execs = 0
_worker_active_pids_est = 0


@dataclass
class ExecutionLimits:
    """Per-process sandbox bounds (one path for all AI-driven World exec)."""

    wall_sec: float
    memory_bytes: int
    cpu_seconds: int
    pids: int
    max_output: int
    network_mode: str  # "none" | "pkg"


@dataclass
class WorldLimits:
    """Per-student World storage / concurrency bounds."""

    max_disk_bytes: int = 2 * 1024 * 1024 * 1024  # 2 GiB — disk fill protection
    max_env_bytes: int = 1024 * 1024 * 1024  # 1 GiB runtimes/bin — install explosion
    max_cache_bytes: int = 512 * 1024 * 1024
    max_concurrent_execs: int = 2  # one student cannot monopolize the worker


@dataclass
class WorkerLimits:
    """Per-worker process bounds (shared host)."""

    max_concurrent_execs: int = 8  # protect host CPU/RAM across students
    max_pids_est: int = 256  # aggregate fork-bomb ceiling
    disk_floor_bytes: int = 512 * 1024 * 1024  # leave free space for others


def execution_limits(*, network_mode: str = "none") -> ExecutionLimits:
    """
    Single secure execution profile.

    wall_sec / cpu_seconds — runaway process (wall-clock + CPU accounting)
    memory_bytes — host OOM / cross-student memory pressure
    pids — fork bomb
    max_output — unbounded stdout/stderr filling worker memory
    network_mode "none" — default: no outbound (SSRF / data exfil)
    network_mode "pkg" — package install only; still wall-capped
    """
    mode = (network_mode or "none").strip().lower()
    if mode not in ("none", "pkg"):
        mode = "none"

    wall = float(getattr(settings, "isolation_timeout_seconds", 45) or 45)
    mem = int(getattr(settings, "isolation_memory_bytes", 512 * 1024 * 1024))
    cpu = int(getattr(settings, "isolation_cpu_seconds", 20) or 20)
    out = int(getattr(settings, "isolation_max_output_bytes", 150_000))

    if mode == "pkg":
        # Installs need network and often more wall time; still hard-capped.
        wall = max(wall, 300.0)
        mem = max(mem, 512 * 1024 * 1024)

    return ExecutionLimits(
        wall_sec=wall,
        memory_bytes=mem,
        cpu_seconds=cpu,
        pids=64,
        max_output=out,
        network_mode=mode,
    )


def world_limits() -> WorldLimits:
    return WorldLimits()


def worker_limits() -> WorkerLimits:
    return WorkerLimits()


# Back-compat names used by discover / older call sites
def world_budget_defaults() -> WorldLimits:
    return world_limits()


def worker_budget_defaults() -> WorkerLimits:
    return worker_limits()


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
    budget: ExecutionLimits,
    world_bud: WorldLimits | None = None,
) -> None:
    """Refuse execution that would exhaust host or world quotas."""
    wb = world_bud or world_limits()
    wk = worker_limits()

    with _lock:
        global _worker_active_execs, _worker_active_pids_est
        if world_concurrent >= wb.max_concurrent_execs:
            raise ResourceDenied(
                "world concurrent execution limit",
                detail=f"{world_concurrent}>={wb.max_concurrent_execs}",
            )
        if _worker_active_execs >= wk.max_concurrent_execs:
            raise ResourceDenied(
                "worker concurrent execution limit",
                detail=f"{_worker_active_execs}>={wk.max_concurrent_execs}",
            )
        if _worker_active_pids_est + budget.pids > wk.max_pids_est:
            raise ResourceDenied(
                "worker pid estimate limit",
                detail=str(_worker_active_pids_est + budget.pids),
            )
        try:
            st = os.statvfs(str(world_root))
            free = st.f_bavail * st.f_frsize
            if free < wk.disk_floor_bytes:
                raise ResourceDenied("host disk floor", detail=str(free))
        except OSError:
            pass
        _worker_active_execs += 1
        _worker_active_pids_est += budget.pids


def release_execution(budget: ExecutionLimits) -> None:
    global _worker_active_execs, _worker_active_pids_est
    with _lock:
        _worker_active_execs = max(0, _worker_active_execs - 1)
        _worker_active_pids_est = max(0, _worker_active_pids_est - budget.pids)


def check_world_disk(world_root, world_bud: WorldLimits | None = None) -> dict[str, Any]:
    wb = world_bud or world_limits()
    used = dir_size(world_root)
    env_used = dir_size(world_root / "runtimes") + dir_size(world_root / "bin")
    if used > wb.max_disk_bytes:
        raise QuotaExceeded("world disk quota exceeded", detail=str(used))
    if env_used > wb.max_env_bytes:
        raise QuotaExceeded("world env size quota exceeded", detail=str(env_used))
    return {"disk_used": used, "env_used": env_used, "max_disk": wb.max_disk_bytes}
