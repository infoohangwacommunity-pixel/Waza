"""
Compatibility adapter: old run_sandboxed → World isolation.

This module is NOT a second security implementation. All execution goes
through wax.world.isolation. No semantic binary allowlist.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wax.world.errors import IsolationUnavailable
from wax.world.isolation import IsolationRequest, run_isolated


@dataclass
class SandboxResult:
    success: bool
    stdout: str
    stderr: str
    exit_code: int | None
    duration_ms: int
    error: str | None = None
    isolation: str = "world"
    cwd: str | None = None


def _world_root_from_cwd(cwd: Path) -> Path:
    cwd = cwd.resolve()
    if (cwd / "identity.json").is_file():
        return cwd
    if (cwd.parent / "identity.json").is_file():
        return cwd.parent
    # worlds/<id>/workspace → worlds/<id>
    if cwd.name in {"workspace", "tmp", "artifacts", "media"} and (cwd.parent / "identity.json").is_file():
        return cwd.parent
    return cwd


async def run_sandboxed(
    argv: list[str],
    *,
    cwd: Path,
    timeout: float | None = None,
    max_output: int | None = None,
) -> SandboxResult:
    world_root = _world_root_from_cwd(Path(cwd))
    try:
        rel = str(Path(cwd).resolve().relative_to(world_root.resolve()))
    except ValueError:
        rel = "workspace"
    if rel in (".", ""):
        rel = "workspace"
    req = IsolationRequest(
        argv=list(argv),
        world_root=world_root,
        cwd_rel=rel if rel != "workspace" else "workspace",
        network_mode="none",
        timeout_sec=float(timeout or 60.0),
        max_output=int(max_output or 150_000),
    )
    try:
        r = await run_isolated(req)
    except IsolationUnavailable as e:
        return SandboxResult(
            False, "", "", None, 0, error=str(e.message), isolation="none", cwd=str(cwd)
        )
    return SandboxResult(
        r.success,
        r.stdout,
        r.stderr,
        r.exit_code,
        r.duration_ms,
        error=r.error,
        isolation=r.backend,
        cwd=str(cwd),
    )
