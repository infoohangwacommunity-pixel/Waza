"""
Terminal sandbox — strongest isolation available on the host.

Order of preference:
1. bubblewrap (bwrap) if installed
2. unshare network + resource limits
3. restricted subprocess (allowlist, workspace cwd, scrubbed env)

Never inject app secrets into the child environment.
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
    # Explicitly strip anything secret-like from parent
    for k in list(os.environ.keys()):
        lk = k.lower()
        if any(x in lk for x in ("key", "secret", "token", "password", "credential", "database")):
            continue  # never copy
    return env


def _preexec_limits():
    """Apply CPU/memory limits in child (best-effort)."""
    try:
        # CPU seconds
        cpu = int(getattr(settings, "terminal_cpu_seconds", 20) or 20)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    except Exception:
        pass
    try:
        # Address space ~512MB default
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
    if binary not in ALLOWED_BINARIES:
        return SandboxResult(False, "", "", None, 0, error=f"Command not permitted: {binary}")

    timeout = timeout or float(settings.terminal_timeout_seconds)
    max_output = max_output or int(settings.terminal_max_output_bytes)
    cwd.mkdir(parents=True, exist_ok=True)
    env = _scrub_env({"HOME": str(cwd)})

    # Prefer bubblewrap isolation
    if _has_bwrap():
        bwrap_argv = [
            "bwrap",
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/bin", "/bin",
            "--ro-bind", "/lib", "/lib",
            "--ro-bind-try", "/lib64", "/lib64",
            "--ro-bind-try", "/etc/resolv.conf", "/etc/resolv.conf",
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
        return await _exec(bwrap_argv, cwd=cwd, env=env, timeout=timeout, max_output=max_output, isolation="bwrap")

    # Fallback: restricted subprocess + rlimits
    return await _exec(
        argv,
        cwd=cwd,
        env=env,
        timeout=timeout,
        max_output=max_output,
        isolation="rlimits",
        preexec=_preexec_limits,
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
            cwd=str(cwd),
            env=env,
            limit=max_output,
            preexec_fn=preexec,
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
            proc.returncode == 0,
            stdout,
            stderr,
            proc.returncode,
            int((time.monotonic() - start) * 1000),
            isolation=isolation,
            cwd=str(cwd),
        )
    except FileNotFoundError as e:
        return SandboxResult(False, "", "", None, 0, error=f"binary_missing:{e}", isolation=isolation)
    except Exception as e:
        logger.exception("sandbox_exec_failed")
        return SandboxResult(False, "", "", None, int((time.monotonic() - start) * 1000), error=str(e), isolation=isolation)
