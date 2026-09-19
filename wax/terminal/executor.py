"""
Safe terminal — the AI's hands over a durable workspace.

Open-world under infrastructure limits (not an educational command menu).
Media lives on disk; the model inspects via paths + allowed tools.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wax.config import get_settings
from wax.observability.logging import get_logger
from wax.terminal.workspace import principal_workspace, work_workspace

logger = get_logger(__name__)
settings = get_settings()

# Binaries useful for inspection / light processing. Still constrained.
ALLOWED_BINARIES = {
    "python3",
    "python",
    "node",
    "echo",
    "date",
    "wc",
    "head",
    "tail",
    "cat",
    "ls",
    "file",
    "stat",
    "uname",
    "ffmpeg",
    "ffprobe",
    "identify",  # ImageMagick
    "convert",
    "tesseract",
    "pdftotext",
    "pdfinfo",
    "sox",
    "mediainfo",
}


@dataclass
class TerminalResult:
    success: bool
    stdout: str
    stderr: str
    exit_code: int | None
    duration_ms: int
    error: str | None = None
    cwd: str | None = None


class TerminalExecutor:
    def __init__(self) -> None:
        self.enabled = settings.terminal_enabled
        self.timeout = settings.terminal_timeout_seconds
        self.max_output = settings.terminal_max_output_bytes
        self.python = settings.terminal_python

    def _resolve_cwd(
        self, principal_id: Any | None = None, work_id: Any | None = None
    ) -> Path:
        if work_id and principal_id:
            return work_workspace(work_id, principal_id)
        if principal_id:
            return principal_workspace(principal_id)
        path = Path("/tmp/wax-term-scratch")
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def run_python(
        self,
        code: str,
        *,
        principal_id: Any | None = None,
        work_id: Any | None = None,
    ) -> TerminalResult:
        if not self.enabled:
            return TerminalResult(False, "", "", None, 0, error="Terminal disabled")
        return await self._run(
            [self.python, "-c", code],
            cwd=self._resolve_cwd(principal_id, work_id),
        )

    async def run_command(
        self,
        argv: list[str],
        *,
        principal_id: Any | None = None,
        work_id: Any | None = None,
    ) -> TerminalResult:
        if not self.enabled:
            return TerminalResult(False, "", "", None, 0, error="Terminal disabled")
        if not argv:
            return TerminalResult(False, "", "", None, 0, error="empty_command")
        binary = os.path.basename(argv[0])
        if binary not in ALLOWED_BINARIES:
            return TerminalResult(
                False,
                "",
                "",
                None,
                0,
                error=f"Command not permitted: {binary}",
            )
        cwd = self._resolve_cwd(principal_id, work_id)
        # Prevent path escape arguments that start with / outside workspace
        safe_argv = [argv[0]]
        for arg in argv[1:]:
            if arg.startswith("/") and not arg.startswith(str(cwd)):
                # only allow absolute paths inside workspace root
                if "/wax-workspaces/" not in arg and not arg.startswith("/tmp/"):
                    return TerminalResult(
                        False, "", "", None, 0, error=f"path_not_allowed:{arg[:80]}"
                    )
            safe_argv.append(arg)
        return await self._run(safe_argv, cwd=cwd)

    async def run_shell_line(
        self,
        line: str,
        *,
        principal_id: Any | None = None,
        work_id: Any | None = None,
    ) -> TerminalResult:
        """Parse a single simple command line (no pipes/redirection for safety)."""
        try:
            argv = shlex.split(line)
        except ValueError as e:
            return TerminalResult(False, "", "", None, 0, error=str(e))
        return await self.run_command(argv, principal_id=principal_id, work_id=work_id)

    async def inspect_media_file(
        self,
        path: str,
        *,
        principal_id: Any | None = None,
    ) -> TerminalResult:
        """Best-effort local inspection without multimodal API."""
        p = Path(path)
        if not p.is_file():
            return TerminalResult(False, "", "", None, 0, error="file_not_found")
        # Try file + ffprobe + optional tesseract for images
        parts: list[str] = []
        r1 = await self.run_command(["file", str(p)], principal_id=principal_id)
        parts.append(f"file: {r1.stdout.strip() or r1.error}")
        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}:
            r2 = await self.run_command(["identify", str(p)], principal_id=principal_id)
            if r2.success:
                parts.append(f"identify: {r2.stdout.strip()}")
            r3 = await self.run_command(
                ["tesseract", str(p), "stdout"], principal_id=principal_id
            )
            if r3.success and r3.stdout.strip():
                parts.append(f"ocr:\n{r3.stdout.strip()[:4000]}")
            elif r3.error:
                parts.append(f"ocr_unavailable: {r3.error or r3.stderr[:200]}")
        if p.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".mp3", ".ogg", ".wav", ".m4a"}:
            r4 = await self.run_command(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_format",
                    "-show_streams",
                    str(p),
                ],
                principal_id=principal_id,
            )
            if r4.success:
                parts.append(f"ffprobe:\n{r4.stdout[:3000]}")
            else:
                parts.append(f"ffprobe_unavailable: {r4.error or r4.stderr[:200]}")
        if p.suffix.lower() == ".pdf":
            r5 = await self.run_command(["pdfinfo", str(p)], principal_id=principal_id)
            if r5.success:
                parts.append(f"pdfinfo:\n{r5.stdout[:2000]}")
            r6 = await self.run_command(
                ["pdftotext", "-l", "3", str(p), "-"], principal_id=principal_id
            )
            if r6.success and r6.stdout.strip():
                parts.append(f"pdftotext:\n{r6.stdout[:4000]}")
        body = "\n\n".join(parts)
        return TerminalResult(
            success=True,
            stdout=body,
            stderr="",
            exit_code=0,
            duration_ms=0,
            cwd=str(p.parent),
        )

    async def _run(self, argv: list[str], cwd: Path | None = None) -> TerminalResult:
        start = time.monotonic()
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(cwd or "/tmp"),
            "LANG": "C.UTF-8",
            "PYTHONIOENCODING": "utf-8",
        }
        workdir = str(cwd or "/tmp")
        try:
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
                return TerminalResult(
                    False,
                    "",
                    "",
                    None,
                    int((time.monotonic() - start) * 1000),
                    error=f"Timed out after {self.timeout}s",
                    cwd=workdir,
                )
            duration = int((time.monotonic() - start) * 1000)
            stdout = (stdout_b or b"")[: self.max_output].decode("utf-8", errors="replace")
            stderr = (stderr_b or b"")[: self.max_output].decode("utf-8", errors="replace")
            return TerminalResult(
                proc.returncode == 0,
                stdout,
                stderr,
                proc.returncode,
                duration,
                cwd=workdir,
            )
        except FileNotFoundError as e:
            return TerminalResult(
                False,
                "",
                "",
                None,
                int((time.monotonic() - start) * 1000),
                error=f"binary_missing:{e}",
                cwd=workdir,
            )
        except Exception as e:
            logger.error("terminal_failure", error=str(e))
            return TerminalResult(
                False,
                "",
                "",
                None,
                int((time.monotonic() - start) * 1000),
                error=str(e),
                cwd=workdir,
            )


def get_terminal() -> TerminalExecutor:
    return TerminalExecutor()
