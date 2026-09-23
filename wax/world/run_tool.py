"""
Run host tool argv inside a principal's World isolation.

Optimized capabilities (ffmpeg, tesseract, …) should call this instead of
raw subprocess so they share the same security boundary as world_exec.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from wax.world.isolation import IsolationRequest, IsolationResult, run_isolated
from wax.world.manager import get_or_create_world


async def run_in_world(
    principal_id: str | None,
    argv: list[str],
    *,
    cwd_rel: str = "workspace",
    network_mode: str = "none",
    timeout_sec: float = 90.0,
    memory_bytes: int = 512 * 1024 * 1024,
    world_root: Path | None = None,
) -> IsolationResult:
    if world_root is None:
        if not principal_id:
            return IsolationResult(
                False, "", "", None, 0, error="no_principal_for_world_exec"
            )
        world = get_or_create_world(str(principal_id))
        world_root = world.root
    return await run_isolated(
        IsolationRequest(
            argv=argv,
            world_root=Path(world_root),
            cwd_rel=cwd_rel,
            network_mode=network_mode,  # type: ignore[arg-type]
            timeout_sec=timeout_sec,
            memory_bytes=memory_bytes,
        )
    )
