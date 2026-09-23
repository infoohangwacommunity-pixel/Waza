"""World execution — isolated process tree, no command allowlist."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from wax.observability.logging import get_logger
from wax.world.errors import WorldError, IsolationUnavailable
from wax.world.isolation import IsolationRequest, run_isolated
from wax.world.manager import World, assert_operable, set_lifecycle
from wax.world.resources import (
    ExecutionBudget,
    admit_execution,
    interactive_budget,
    batch_budget,
    release_execution,
)

logger = get_logger(__name__)


async def world_exec(
    world: World,
    *,
    argv: list[str] | None = None,
    script: str | None = None,
    runtime: str = "python",
    cwd_rel: str = "workspace",
    network_mode: str = "none",
    budget_class: str = "interactive",
    env_extra: dict[str, str] | None = None,
) -> dict[str, Any]:
    assert_operable(world)
    if budget_class == "batch":
        budget = batch_budget()
    else:
        budget = interactive_budget()
    if network_mode in ("none", "pkg"):
        budget.network_mode = network_mode

    if script is not None:
        # write script into world tmp and run with runtime
        exec_id = uuid.uuid4().hex[:12]
        script_rel = f"tmp/_wax_script_{exec_id}.py"
        script_path = world.root / script_rel
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(script, encoding="utf-8")
        py = _python_bin(world)
        argv = [py, str(script_path)]
    if not argv:
        return {"ok": False, "error": "argv_or_script_required"}

    admit_execution(world_root=world.root, world_concurrent=world.active_execs, budget=budget)
    world.active_execs += 1
    if world.lifecycle == "READY":
        set_lifecycle(world, "BUSY", "execution active")
    exec_id = uuid.uuid4().hex[:16]
    rec = {
        "execution_id": exec_id,
        "argv": argv[:20],
        "status": "running",
        "started_at": time.time(),
    }
    (world.root / "state" / "exec" / f"{exec_id}.json").write_text(json.dumps(rec), encoding="utf-8")

    try:
        # Prefer world venv python on PATH via isolation HOME/bin
        req = IsolationRequest(
            argv=argv,
            world_root=world.root,
            cwd_rel=cwd_rel,
            network_mode=budget.network_mode,  # type: ignore[arg-type]
            timeout_sec=budget.wall_sec,
            max_output=budget.max_output,
            memory_bytes=budget.memory_bytes,
            cpu_seconds=budget.cpu_seconds,
            pids_limit=budget.pids,
            env_extra=env_extra or {},
        )
        # Put world venv bin first if present
        venv_bin = world.root / "runtimes" / "python" / "default" / "bin"
        if venv_bin.is_dir():
            req.env_extra = {
                **(req.env_extra or {}),
                "PATH": f"{venv_bin}:{world.root}/bin:/usr/local/bin:/usr/bin:/bin",
                "VIRTUAL_ENV": str(world.root / "runtimes" / "python" / "default"),
            }
        result = await run_isolated(req)
        rec.update(
            {
                "status": "ok" if result.success else "failed",
                "exit_code": result.exit_code,
                "duration_ms": result.duration_ms,
                "backend": result.backend,
                "isolation_level": result.isolation_level,
                "error": result.error,
                "finished_at": time.time(),
            }
        )
        (world.root / "state" / "exec" / f"{exec_id}.json").write_text(json.dumps(rec), encoding="utf-8")
        return {
            "ok": result.success,
            "execution_id": exec_id,
            "stdout": result.stdout[:8000],
            "stderr": result.stderr[:4000],
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
            "backend": result.backend,
            "isolation_level": result.isolation_level,
            "error": result.error,
        }
    except IsolationUnavailable as e:
        return e.to_dict()
    except WorldError as e:
        return e.to_dict()
    finally:
        world.active_execs = max(0, world.active_execs - 1)
        release_execution(budget)
        if world.active_execs == 0 and world.lifecycle == "BUSY":
            set_lifecycle(world, "READY", "")


def _python_bin(world: World) -> str:
    for cand in (
        world.root / "runtimes" / "python" / "default" / "bin" / "python",
        world.root / "runtimes" / "python" / "default" / "bin" / "python3",
    ):
        if cand.is_file():
            return str(cand)
    return "python3"
