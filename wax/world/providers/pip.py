"""Pip provider — installs into world Python runtime only (never WAX app env)."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from wax.world.isolation import IsolationRequest, run_isolated
from wax.world.providers.base import AcquirePlan, AcquireRequest, AcquireResult
from wax.world.resources import acquire_budget


class PipProvider:
    name = "pip"

    def can_handle(self, req: AcquireRequest) -> bool:
        return req.kind in ("python_package", "pip")

    async def plan(self, req: AcquireRequest) -> AcquirePlan:
        return AcquirePlan(
            steps=[
                {"op": "ensure_venv"},
                {"op": "pip_install", "name": req.name, "version": req.version_spec},
                {"op": "verify_import"},
            ],
            network_mode="pkg",
        )

    async def install(self, req: AcquireRequest, world_root: Path, plan: AcquirePlan) -> AcquireResult:
        budget = acquire_budget()
        venv = world_root / "runtimes" / "python" / "default"
        venv_py = venv / "bin" / "python"
        if not venv_py.is_file():
            # create venv using system python inside isolation
            r = await run_isolated(
                IsolationRequest(
                    argv=["python3", "-m", "venv", str(venv)],
                    world_root=world_root,
                    cwd_rel="workspace",
                    network_mode="none",
                    timeout_sec=120,
                    memory_bytes=budget.memory_bytes,
                    cpu_seconds=60,
                    pids_limit=32,
                )
            )
            if not r.success or not venv_py.is_file():
                return AcquireResult(
                    ok=False,
                    error="venv_create_failed",
                    detail=(r.stderr or r.error or "")[:400],
                )

        spec = req.name if not req.version_spec else f"{req.name}{req.version_spec}"
        # sanitize name roughly (no shell metacharacters)
        if not re.match(r"^[A-Za-z0-9_.\-\[\],<>=!]+$", spec):
            return AcquireResult(ok=False, error="invalid_package_spec")

        # Install in isolation with network=pkg (bwrap without --unshare-net)
        r = await run_isolated(
            IsolationRequest(
                argv=[
                    str(venv_py),
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "--no-input",
                    "--cache-dir",
                    str(world_root / "cache" / "pip"),
                    spec,
                ],
                world_root=world_root,
                cwd_rel="workspace",
                network_mode="pkg",
                timeout_sec=budget.wall_sec,
                memory_bytes=budget.memory_bytes,
                cpu_seconds=budget.cpu_seconds,
                pids_limit=budget.pids,
                env_extra={
                    "PATH": f"{venv / 'bin'}:/usr/local/bin:/usr/bin:/bin",
                    "VIRTUAL_ENV": str(venv),
                    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                },
            )
        )
        if not r.success:
            return AcquireResult(
                ok=False,
                error="pip_install_failed",
                detail=(r.stderr or r.stdout or r.error or "")[:600],
            )

        # Verify: observed reality — import module (best-effort module name)
        mod = req.name.replace("-", "_").split("[")[0]
        v = await run_isolated(
            IsolationRequest(
                argv=[str(venv_py), "-c", f"import {mod}; print('ok')"],
                world_root=world_root,
                cwd_rel="workspace",
                network_mode="none",
                timeout_sec=30,
                memory_bytes=budget.memory_bytes,
                env_extra={"PATH": f"{venv / 'bin'}:/usr/bin:/bin", "VIRTUAL_ENV": str(venv)},
            )
        )
        verify_status = "ok" if v.success and "ok" in (v.stdout or "") else "import_failed"
        observed = {
            "kind": "python_package",
            "name": req.name,
            "version_spec": req.version_spec,
            "provider": self.name,
            "runtime": "python/default",
            "verify_status": verify_status,
            "verified_at": time.time(),
            "import_module": mod,
        }
        if verify_status != "ok":
            return AcquireResult(
                ok=False,
                error="verification_failed",
                detail=(v.stderr or v.stdout or "")[:400],
                observed=observed,
            )
        return AcquireResult(ok=True, observed=observed)
