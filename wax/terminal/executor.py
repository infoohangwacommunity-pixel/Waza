"""
Safe terminal — AI hands over durable workspace with sandbox isolation.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wax.config import get_settings
from wax.observability.logging import get_logger
from wax.terminal.sandbox import run_sandboxed
from wax.terminal.workspace import principal_workspace, work_workspace

logger = get_logger(__name__)
settings = get_settings()

# Safety ceilings for document extraction (intelligence may request less)
DEFAULT_PDF_MAX_PAGES = 20
HARD_PDF_MAX_PAGES = 50


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
    # Structured fields (optional; tools prefer these)
    structured: dict[str, Any] = field(default_factory=dict)


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
        """Path hygiene only — no binary allowlist. Prefer World isolation."""
        if not argv:
            return None
        safe = [argv[0]]
        for arg in argv[1:]:
            if arg.startswith("/") and "/wax-workspaces/" not in arg and not arg.startswith("/tmp/"):
                if str(cwd.resolve()) not in arg and "/wax-artifacts/" not in arg:
                    return None
            safe.append(arg)
        return safe

    async def run_python(
        self, code: str, *, principal_id: Any | None = None, work_id: Any | None = None
    ) -> TerminalResult:
        if not self.enabled:
            return TerminalResult(False, "", "", None, 0, error="Terminal disabled")
        cwd = self._resolve_cwd(principal_id, work_id)
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
        self,
        path: str,
        *,
        principal_id: Any | None = None,
        extract_text: bool = False,
        max_pages: int | None = None,
    ) -> TerminalResult:
        """
        Basic file inspection. Specialized OCR/transcription is decided by the AI
        via world_exec (install tools, run commands) — not hidden infrastructure.
        """
        p = Path(path)
        if not p.is_file():
            return TerminalResult(False, "", "", None, 0, error="file_not_found")
        size = p.stat().st_size
        suffix = p.suffix.lower()
        parts = [
            f"path: {p}",
            f"size_bytes: {size}",
            f"suffix: {suffix}",
        ]
        # Lightweight document text extraction when explicitly requested
        if extract_text and suffix == ".pdf":
            r = await self.run_command(
                ["pdftotext", "-l", str(max_pages or 5), str(p), "-"],
                principal_id=principal_id,
            )
            if r.success and r.stdout.strip():
                parts.append("pdftotext:\n" + r.stdout.strip()[:4000])
            else:
                parts.append("pdftotext: unavailable or empty")
        elif extract_text and suffix in (".txt", ".md", ".csv", ".json", ".py", ".html"):
            try:
                content = p.read_text(encoding="utf-8", errors="replace")[:8000]
                parts.append("text:\n" + content)
            except Exception as e:
                parts.append(f"read_error: {e}")
        return TerminalResult(True, "\n".join(parts), "", None, 0)

