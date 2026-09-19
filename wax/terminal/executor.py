"""
Safe terminal — AI hands over durable workspace with sandbox isolation.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wax.config import get_settings
from wax.observability.logging import get_logger
from wax.terminal.sandbox import ALLOWED_BINARIES, run_sandboxed
from wax.terminal.workspace import principal_workspace, work_workspace

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
    cwd: str | None = None
    isolation: str | None = None


class TerminalExecutor:
    def __init__(self) -> None:
        self.enabled = settings.terminal_enabled
        self.timeout = settings.terminal_timeout_seconds
        self.max_output = settings.terminal_max_output_bytes
        self.python = settings.terminal_python

    def _resolve_cwd(self, principal_id: Any | None = None, work_id: Any | None = None) -> Path:
        if work_id and principal_id:
            return work_workspace(work_id, principal_id)
        if principal_id:
            return principal_workspace(principal_id)
        path = Path("/tmp/wax-term-scratch")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _guard_args(self, argv: list[str], cwd: Path) -> list[str] | None:
        if not argv:
            return None
        if os.path.basename(argv[0]) not in ALLOWED_BINARIES:
            return None
        safe = [argv[0]]
        for arg in argv[1:]:
            if arg.startswith("/") and "/wax-workspaces/" not in arg and not arg.startswith("/tmp/"):
                # allow absolute only inside workspace roots
                if not str(cwd.resolve()) in arg and "/wax-artifacts/" not in arg:
                    return None
            safe.append(arg)
        return safe

    async def run_python(
        self, code: str, *, principal_id: Any | None = None, work_id: Any | None = None
    ) -> TerminalResult:
        if not self.enabled:
            return TerminalResult(False, "", "", None, 0, error="Terminal disabled")
        cwd = self._resolve_cwd(principal_id, work_id)
        # Write code to workspace file then execute — avoids shell injection
        script = cwd / "_wax_run.py"
        script.write_text(code, encoding="utf-8")
        return await self.run_command(
            [self.python, str(script)], principal_id=principal_id, work_id=work_id
        )

    async def run_command(
        self, argv: list[str], *, principal_id: Any | None = None, work_id: Any | None = None
    ) -> TerminalResult:
        if not self.enabled:
            return TerminalResult(False, "", "", None, 0, error="Terminal disabled")
        cwd = self._resolve_cwd(principal_id, work_id)
        safe = self._guard_args(argv, cwd)
        if not safe:
            return TerminalResult(
                False, "", "", None, 0,
                error=f"Command not permitted: {argv[0] if argv else 'empty'}",
            )
        r = await run_sandboxed(safe, cwd=cwd, timeout=float(self.timeout), max_output=self.max_output)
        return TerminalResult(
            r.success, r.stdout, r.stderr, r.exit_code, r.duration_ms,
            error=r.error, cwd=r.cwd, isolation=r.isolation,
        )

    async def run_shell_line(
        self, line: str, *, principal_id: Any | None = None, work_id: Any | None = None
    ) -> TerminalResult:
        try:
            argv = shlex.split(line)
        except ValueError as e:
            return TerminalResult(False, "", "", None, 0, error=str(e))
        return await self.run_command(argv, principal_id=principal_id, work_id=work_id)

    async def inspect_media_file(
        self, path: str, *, principal_id: Any | None = None
    ) -> TerminalResult:
        p = Path(path)
        if not p.is_file():
            return TerminalResult(False, "", "", None, 0, error="file_not_found")
        parts: list[str] = []
        r1 = await self.run_command(["file", str(p)], principal_id=principal_id)
        parts.append(f"file: {r1.stdout.strip() or r1.error}")
        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}:
            r2 = await self.run_command(["identify", str(p)], principal_id=principal_id)
            if r2.success:
                parts.append(f"identify: {r2.stdout.strip()}")
            r3 = await self.run_command(["tesseract", str(p), "stdout"], principal_id=principal_id)
            if r3.success and r3.stdout.strip():
                parts.append(f"ocr:\n{r3.stdout.strip()[:4000]}")
        if p.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".mp3", ".ogg", ".wav", ".m4a"}:
            r4 = await self.run_command(
                ["ffprobe", "-v", "error", "-show_format", "-show_streams", str(p)],
                principal_id=principal_id,
            )
            if r4.success:
                parts.append(f"ffprobe:\n{r4.stdout[:3000]}")
        if p.suffix.lower() == ".pdf":
            r5 = await self.run_command(["pdfinfo", str(p)], principal_id=principal_id)
            if r5.success:
                parts.append(f"pdfinfo:\n{r5.stdout[:2000]}")
            r6 = await self.run_command(
                ["pdftotext", "-l", "3", str(p), "-"], principal_id=principal_id
            )
            if r6.success and r6.stdout.strip():
                parts.append(f"pdftotext:\n{r6.stdout[:4000]}")
        return TerminalResult(True, "\n\n".join(parts), "", 0, 0, cwd=str(p.parent))


def get_terminal() -> TerminalExecutor:
    return TerminalExecutor()
