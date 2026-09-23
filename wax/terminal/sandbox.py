"""
Terminal sandbox — strongest isolation available on the host.

Order of preference:
1. docker (if WAX_TERMINAL_DOCKER=1 or terminal_require_sandbox + docker present)
2. bubblewrap (bwrap)
3. unshare + rlimits (dev only unless require_sandbox is false)

Never inject app secrets into the child environment.
Multi-tenant: each principal gets a dedicated workspace directory.
"""

from __future__ import annotations

import asyncio
import os
import resource
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wax.config import get_settings
from wax.observability.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

ALLOWED_BINARIES = {
    "python3", "python", "node", "echo", "date", "wc", "head", "tail", "cat", "ls",
    "file", "stat", "uname", "ffmpeg", "ffprobe", "identify", "convert", "tesseract",
    "pdftotext", "pdfinfo", "sox", "mediainfo", "bash", "sh",
}


@dataclass
class SandboxResult:
    success: bool
    stdout: str
    stderr: str
    exit_code: int | None
    duration_ms: int
    error: str | None = None
    isolation: str = "subprocess"
    cwd: str | None = None


def _scrub_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        "HOME": "/tmp",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if extra:
        env.update(extra)
    return env


def _preexec_limits():
    try:
        cpu = int(getattr(settings, "terminal_cpu_seconds", 20) or 20)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    except Exception:
        pass
    try:
        mem = int(getattr(settings, "terminal_memory_bytes", 512 * 1024 * 1024) or 512 * 1024 * 1024)
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    except Exception:
        pass
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    except Exception:
        pass
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
    except Exception:
        pass


def _has_bwrap() -> bool:
    return shutil.which("bwrap") is not None


def _has_docker() -> bool:
    return shutil.which("docker") is not None


def _want_docker() -> bool:
    if os.environ.get("WAX_TERMINAL_DOCKER", "").lower() in ("1", "true", "yes"):
        return True
    return bool(getattr(settings, "terminal_use_docker", False))


async def run_sandboxed(
    argv: list[str],
    *,
    cwd: Path,
    timeout: float | None = None,
    max_output: int | None = None,
) -> SandboxResult:
    if not argv:
        return SandboxResult(False, "", "", None, 0, error="empty_command")
    binary = os.path.basename(argv[0])
    # Legacy path: allowlist remains for old workspace_command only.
    # World execution uses wax.world.isolation (no semantic allowlist).
    # Set WAX_DISABLE_BINARY_ALLOWLIST=1 to disable this product restriction during migration.
    import os as _os
    if _os.environ.get("WAX_DISABLE_BINARY_ALLOWLIST", "").lower() not in ("1", "true", "yes"):
        if binary not in ALLOWED_BINARIES:
            return SandboxResult(False, "", "", None, 0, error=f"Command not permitted: {binary}")

    timeout = timeout or float(settings.terminal_timeout_seconds)
    max_output = max_output or int(settings.terminal_max_output_bytes)
    cwd.mkdir(parents=True, exist_ok=True)
    env = _scrub_env({"HOME": str(cwd)})
    # Production always requires docker/bwrap — never silent rlimits fallback
    require = bool(getattr(settings, "effective_terminal_require_sandbox", False))
    if not require:
        # property may exist
        try:
            require = bool(settings.effective_terminal_require_sandbox)
        except Exception:
            require = bool(getattr(settings, "terminal_require_sandbox", False))
            if getattr(settings, "app_env", "") == "production":
                require = True


    # 1) Docker isolation (strong multi-tenant boundary when available)
    if _want_docker() and _has_docker():
        image = os.environ.get("WAX_TERMINAL_IMAGE", "python:3.12-slim")
        # Mount only the principal workspace; no network; drop caps; read-only root
        docker_argv = [
            "docker", "run", "--rm",
            "--network", "none",
            "--read-only",
            "--tmpfs", "/tmp:rw,size=64m",
            "--memory", str(int(getattr(settings, "terminal_memory_bytes", 512 * 1024 * 1024))),
            "--cpus", "0.5",
            "--pids-limit", "64",
            "--security-opt", "no-new-privileges",
            "--cap-drop", "ALL",
            "-v", f"{cwd.resolve()}:/workspace:rw",
            "-w", "/workspace",
            "-e", "HOME=/workspace",
            "-e", "LANG=C.UTF-8",
            image,
            *argv,
        ]
        return await _exec(
            docker_argv, cwd=cwd, env=env, timeout=timeout, max_output=max_output, isolation="docker"
        )

    # 2) bubblewrap
    if _has_bwrap():
        bwrap_argv = [
            "bwrap",
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/bin", "/bin",
            "--ro-bind", "/lib", "/lib",
            "--ro-bind-try", "/lib64", "/lib64",
            "--bind", str(cwd), str(cwd),
            "--chdir", str(cwd),
            "--unshare-net",
            "--unshare-pid",
            "--die-with-parent",
            "--new-session",
            "--tmpfs", "/tmp",
            "--dev", "/dev",
            "--proc", "/proc",
            "--setenv", "HOME", str(cwd),
            "--setenv", "PATH", "/usr/bin:/bin",
            "--setenv", "LANG", "C.UTF-8",
            "--",
            *argv,
        ]
        return await _exec(
            bwrap_argv, cwd=cwd, env=env, timeout=timeout, max_output=max_output, isolation="bwrap"
        )

    if require:
        return SandboxResult(
            False, "", "", None, 0,
            error="sandbox_required_but_unavailable (install bwrap or enable docker)",
            isolation="none",
        )

    # 3) Dev fallback: rlimits only
    return await _exec(
        argv, cwd=cwd, env=env, timeout=timeout, max_output=max_output,
        isolation="rlimits", preexec=_preexec_limits,
    )


async def _exec(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float,
    max_output: int,
    isolation: str,
    preexec=None,
) -> SandboxResult:
    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd) if isolation != "docker" else None,
            env=env if isolation != "docker" else None,
            limit=max_output,
            preexec_fn=preexec if isolation == "rlimits" else None,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return SandboxResult(
                False, "", "", None,
                int((time.monotonic() - start) * 1000),
                error=f"Timed out after {timeout}s",
                isolation=isolation,
                cwd=str(cwd),
            )
        stdout = (stdout_b or b"")[:max_output].decode("utf-8", errors="replace")
        stderr = (stderr_b or b"")[:max_output].decode("utf-8", errors="replace")
        return SandboxResult(
            proc.returncode == 0, stdout, stderr, proc.returncode,
            int((time.monotonic() - start) * 1000),
            isolation=isolation, cwd=str(cwd),
        )
    except FileNotFoundError as e:
        return SandboxResult(False, "", "", None, 0, error=f"binary_missing:{e}", isolation=isolation)
    except Exception as e:
        logger.exception("sandbox_exec_failed")
        return SandboxResult(
            False, "", "", None, int((time.monotonic() - start) * 1000),
            error=str(e), isolation=isolation,
        )
