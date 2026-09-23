"""
Isolation backend — infrastructure boundary, not a command allowlist.

No ALLOWED_BINARIES. Security is mounts, namespaces, caps, budgets, scrubbed env.
Production: fail closed if no secure backend.

Capability levels:
  full     — docker (stronger cgroup) when available and requested
  standard — bubblewrap (primary on Railway)
"""

from __future__ import annotations

import asyncio
import os
import resource
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from wax.config import get_settings
from wax.observability.logging import get_logger
from wax.world.errors import IsolationUnavailable, ExecutionTimeout

logger = get_logger(__name__)
settings = get_settings()

NetworkMode = Literal["none", "pkg"]
IsolationLevel = Literal["full", "standard", "dev_rlimits"]


@dataclass
class IsolationResult:
    success: bool
    stdout: str
    stderr: str
    exit_code: int | None
    duration_ms: int
    error: str | None = None
    isolation_level: str = "none"
    backend: str = "none"


@dataclass
class IsolationRequest:
    argv: list[str]
    world_root: Path
    cwd_rel: str = "workspace"
    network_mode: NetworkMode = "none"
    timeout_sec: float = 60.0
    max_output: int = 150_000
    memory_bytes: int = 512 * 1024 * 1024
    cpu_seconds: int = 30
    pids_limit: int = 64
    env_extra: dict[str, str] = field(default_factory=dict)
    # RO binds for shared read-only assets (e.g. models)
    ro_binds: list[tuple[str, str]] = field(default_factory=list)


def _scrub_env(home: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        "PATH": f"{home}/bin:/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "HOME": home,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    }
    if extra:
        # never allow overriding critical secrets from parent
        blocked = {
            "DATABASE_URL",
            "PRIMARY_API_KEY",
            "OPENAI_API_KEY",
            "TELEGRAM_BOT_TOKEN",
            "WHATSAPP_TOKEN",
            "WAX_PRIMARY_API_KEY",
        }
        for k, v in extra.items():
            if k.upper() in blocked or k.upper().endswith("_API_KEY"):
                continue
            env[k] = v
    return env


def _require_sandbox() -> bool:
    if getattr(settings, "app_env", "") == "production":
        return True
    try:
        return bool(settings.effective_terminal_require_sandbox)
    except Exception:
        return bool(getattr(settings, "terminal_require_sandbox", False))


def _has_bwrap() -> bool:
    return shutil.which("bwrap") is not None


def _has_docker() -> bool:
    return shutil.which("docker") is not None


def _want_docker() -> bool:
    if os.environ.get("WAX_TERMINAL_DOCKER", "").lower() in ("1", "true", "yes"):
        return True
    return bool(getattr(settings, "terminal_use_docker", False))


def _preexec_limits(memory_bytes: int, cpu_seconds: int, pids: int):
    def _inner():
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        except Exception:
            pass
        try:
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        except Exception:
            pass
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        except Exception:
            pass
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (pids, pids))
        except Exception:
            pass

    return _inner


async def run_isolated(req: IsolationRequest) -> IsolationResult:
    if not req.argv:
        return IsolationResult(False, "", "", None, 0, error="empty_argv")

    world = req.world_root.resolve()
    if not world.is_dir():
        return IsolationResult(False, "", "", None, 0, error="world_root_missing")

    cwd = (world / req.cwd_rel).resolve()
    try:
        cwd.relative_to(world)
    except ValueError:
        return IsolationResult(False, "", "", None, 0, error="cwd_escape")
    cwd.mkdir(parents=True, exist_ok=True)

    env = _scrub_env(str(world), req.env_extra)

    if _want_docker() and _has_docker():
        return await _run_docker(req, world, cwd, env)
    if _has_bwrap():
        return await _run_bwrap(req, world, cwd, env)

    if _require_sandbox():
        raise IsolationUnavailable(
            "No secure isolation backend (bwrap/docker)",
            detail="Install bubblewrap or enable docker; production refuses host exec",
        )

    # Dev-only rlimits fallback
    return await _run_rlimits(req, world, cwd, env)


async def _run_docker(
    req: IsolationRequest, world: Path, cwd: Path, env: dict[str, str]
) -> IsolationResult:
    image = os.environ.get("WAX_TERMINAL_IMAGE", "python:3.12-slim")
    net = "none" if req.network_mode == "none" else "bridge"
    docker_argv = [
        "docker",
        "run",
        "--rm",
        "--network",
        net,
        "--read-only",
        "--tmpfs",
        "/tmp:rw,size=64m",
        "--memory",
        str(req.memory_bytes),
        "--cpus",
        "0.5",
        "--pids-limit",
        str(req.pids_limit),
        "--security-opt",
        "no-new-privileges",
        "--cap-drop",
        "ALL",
        "-v",
        f"{world}:/world:rw",
        "-w",
        f"/world/{req.cwd_rel}",
        "-e",
        "HOME=/world",
        "-e",
        "LANG=C.UTF-8",
        "-e",
        "PATH=/world/bin:/usr/local/bin:/usr/bin:/bin",
    ]
    for host, cont in req.ro_binds:
        docker_argv.extend(["-v", f"{host}:{cont}:ro"])
    docker_argv.append(image)
    docker_argv.extend(req.argv)
    return await _exec(
        docker_argv,
        cwd=None,
        env=None,
        timeout=req.timeout_sec,
        max_output=req.max_output,
        level="full",
        backend="docker",
    )


async def _run_bwrap(
    req: IsolationRequest, world: Path, cwd: Path, env: dict[str, str]
) -> IsolationResult:
    # Standard isolation profile for Railway
    bwrap: list[str] = [
        "bwrap",
        "--ro-bind",
        "/usr",
        "/usr",
        "--ro-bind",
        "/bin",
        "/bin",
        "--ro-bind",
        "/lib",
        "/lib",
        "--ro-bind-try",
        "/lib64",
        "/lib64",
        "--ro-bind-try",
        "/usr/local",
        "/usr/local",
        "--bind",
        str(world),
        str(world),
        "--chdir",
        str(cwd),
        "--die-with-parent",
        "--new-session",
        "--unshare-pid",
        "--unshare-ipc",
        "--tmpfs",
        "/tmp",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--setenv",
        "HOME",
        str(world),
        "--setenv",
        "PATH",
        f"{world}/bin:/usr/local/bin:/usr/bin:/bin",
        "--setenv",
        "LANG",
        "C.UTF-8",
        "--setenv",
        "PYTHONDONTWRITEBYTECODE",
        "1",
        "--setenv",
        "PYTHONNOUSERSITE",
        "1",
    ]
    if req.network_mode == "none":
        bwrap.append("--unshare-net")
    for host, cont in req.ro_binds:
        bwrap.extend(["--ro-bind-try", host, cont])
    # Do not bind docker.sock, other worlds, or app secrets paths
    bwrap.append("--")
    bwrap.extend(req.argv)
    return await _exec(
        bwrap,
        cwd=cwd,
        env=env,
        timeout=req.timeout_sec,
        max_output=req.max_output,
        level="standard",
        backend="bwrap",
        preexec=_preexec_limits(req.memory_bytes, req.cpu_seconds, req.pids_limit),
    )


async def _run_rlimits(
    req: IsolationRequest, world: Path, cwd: Path, env: dict[str, str]
) -> IsolationResult:
    return await _exec(
        req.argv,
        cwd=cwd,
        env=env,
        timeout=req.timeout_sec,
        max_output=req.max_output,
        level="dev_rlimits",
        backend="rlimits",
        preexec=_preexec_limits(req.memory_bytes, req.cpu_seconds, req.pids_limit),
    )


async def _exec(
    argv: list[str],
    *,
    cwd: Path | None,
    env: dict[str, str] | None,
    timeout: float,
    max_output: int,
    level: str,
    backend: str,
    preexec=None,
) -> IsolationResult:
    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd) if cwd else None,
            env=env,
            limit=max_output,
            preexec_fn=preexec,
            start_new_session=True,  # process group for cleanup
        )
        try:
            out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            # Kill process group when possible
            try:
                import signal

                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            try:
                await proc.wait()
            except Exception:
                pass
            return IsolationResult(
                False,
                "",
                "",
                None,
                int((time.monotonic() - start) * 1000),
                error=f"Timed out after {timeout}s",
                isolation_level=level,
                backend=backend,
            )
        stdout = (out_b or b"")[:max_output].decode("utf-8", errors="replace")
        stderr = (err_b or b"")[:max_output].decode("utf-8", errors="replace")
        return IsolationResult(
            proc.returncode == 0,
            stdout,
            stderr,
            proc.returncode,
            int((time.monotonic() - start) * 1000),
            isolation_level=level,
            backend=backend,
        )
    except FileNotFoundError as e:
        return IsolationResult(
            False, "", "", None, 0, error=f"binary_missing:{e}", isolation_level=level, backend=backend
        )
    except Exception as e:
        logger.exception("isolation_exec_failed")
        return IsolationResult(
            False,
            "",
            "",
            None,
            int((time.monotonic() - start) * 1000),
            error=str(e)[:300],
            isolation_level=level,
            backend=backend,
        )
