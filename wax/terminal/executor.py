"""
Safe terminal / computation mechanism.

Open-world capability for the tutor, with hard infrastructure limits.
Not an educational command list — general execution under isolation.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import dataclass
from typing import Any

from wax.config import get_settings
from wax.observability.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


@dataclass
class TerminalResult:
    success: bool
    stdout: str
    stderr: str
    exit_code: int | None
    duration_ms: int
    error: str | None = None


class TerminalExecutor:
    """
    Restricted subprocess execution.
    - Time limit
    - Output size limit
    - No inherited secrets
    - Temporary workdir
    """

    def __init__(self) -> None:
        self.enabled = settings.terminal_enabled
        self.timeout = settings.terminal_timeout_seconds
        self.max_output = settings.terminal_max_output_bytes
        self.python = settings.terminal_python

    async def run_python(self, code: str) -> TerminalResult:
        if not self.enabled:
            return TerminalResult(
                success=False,
                stdout="",
                stderr="",
                exit_code=None,
                duration_ms=0,
                error="Terminal disabled",
            )
        return await self._run([self.python, "-c", code])

    async def run_command(self, argv: list[str]) -> TerminalResult:
        if not self.enabled:
            return TerminalResult(
                success=False,
                stdout="",
                stderr="",
                exit_code=None,
                duration_ms=0,
                error="Terminal disabled",
            )
        # Extremely conservative: only allow a small allowlist of safe binaries for v1
        allowed = {"python3", "python", "node", "echo", "date", "wc", "head", "tail"}
        if not argv or os.path.basename(argv[0]) not in allowed:
            return TerminalResult(
                success=False,
                stdout="",
                stderr="",
                exit_code=None,
                duration_ms=0,
                error=f"Command not permitted: {argv[0] if argv else 'empty'}",
            )
        return await self._run(argv)

    async def _run(self, argv: list[str]) -> TerminalResult:
        import time

        start = time.monotonic()
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": "/tmp",
            "LANG": "C.UTF-8",
        }
        # Never pass application secrets into the terminal environment
        try:
            with tempfile.TemporaryDirectory(prefix="wax-term-") as workdir:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=workdir,
                    env=env,
                    limit=self.max_output,
                )
                try:
                    stdout_b, stderr_b = await asyncio.wait_for(
                        proc.communicate(), timeout=self.timeout
                    )
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                    duration = int((time.monotonic() - start) * 1000)
                    return TerminalResult(
                        success=False,
                        stdout="",
                        stderr="",
                        exit_code=None,
                        duration_ms=duration,
                        error=f"Timed out after {self.timeout}s",
                    )
                duration = int((time.monotonic() - start) * 1000)
                stdout = (stdout_b or b"")[: self.max_output].decode("utf-8", errors="replace")
                stderr = (stderr_b or b"")[: self.max_output].decode("utf-8", errors="replace")
                return TerminalResult(
                    success=proc.returncode == 0,
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=proc.returncode,
                    duration_ms=duration,
                )
        except Exception as e:
            duration = int((time.monotonic() - start) * 1000)
            logger.error("terminal_failure", error=str(e))
            return TerminalResult(
                success=False,
                stdout="",
                stderr="",
                exit_code=None,
                duration_ms=duration,
                error=str(e),
            )


def get_terminal() -> TerminalExecutor:
    return TerminalExecutor()
