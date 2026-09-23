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
from wax.terminal.sandbox import ALLOWED_BINARIES, run_sandboxed
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
        if not argv:
            return None
        if os.path.basename(argv[0]) not in ALLOWED_BINARIES:
            return None
        safe = [argv[0]]
        for arg in argv[1:]:
            if arg.startswith("/") and "/wax-workspaces/" not in arg and not arg.startswith("/tmp/"):
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
        extract_text: bool = True,
        max_pages: int | None = None,
    ) -> TerminalResult:
        """
        Probe media + optionally extract text (OCR / pdftotext) when useful.

        Returns TerminalResult with structured MediaProbe + optional evidence.
        Does NOT auto-transcribe audio or call vision — intelligence decides.
        """
        from wax.media.probe import probe_local_file
        from wax.media.types import ExtractionEvidence

        p = Path(path)
        if not p.is_file():
            return TerminalResult(False, "", "", None, 0, error="file_not_found")

        probe = probe_local_file(p)
        parts: list[str] = [
            f"kind: {probe.kind}",
            f"capabilities: {', '.join(probe.capabilities.as_list())}",
            f"size_bytes: {probe.size_bytes}",
        ]
        if probe.file_description:
            parts.append(f"file: {probe.file_description}")
        if probe.duration_sec is not None:
            parts.append(f"duration_sec: {probe.duration_sec}")
        if probe.page_count is not None:
            parts.append(f"page_count: {probe.page_count}")
        if probe.width and probe.height:
            parts.append(f"dimensions: {probe.width}x{probe.height}")

        evidence: list[dict[str, Any]] = []
        pages = max_pages if max_pages is not None else DEFAULT_PDF_MAX_PAGES
        pages = max(1, min(int(pages), HARD_PDF_MAX_PAGES))

        # Optional OCR for images (capability exists; running extract_text is a tool choice)
        if extract_text and probe.kind == "image" and probe.capabilities.ocr:
            r3 = await self.run_command(["tesseract", str(p), "stdout"], principal_id=principal_id)
            if r3.success and r3.stdout.strip():
                text = r3.stdout.strip()[:8000]
                parts.append(f"ocr:\n{text[:4000]}")
                status = "usable" if len(text) > 20 else "uncertain"
                evidence.append(
                    ExtractionEvidence(
                        kind="ocr",
                        processor="tesseract",
                        payload={"text": text},
                        quality_status=status,  # type: ignore[arg-type]
                        provenance={"path": str(p.resolve())},
                    ).to_dict()
                )
            elif extract_text:
                evidence.append(
                    ExtractionEvidence(
                        kind="ocr",
                        processor="tesseract",
                        payload={"text": ""},
                        quality_status="unusable",
                        warnings=["ocr_empty_or_failed"],
                        provenance={"path": str(p.resolve())},
                    ).to_dict()
                )

        if extract_text and probe.kind == "document" and p.suffix.lower() == ".pdf":
            r5 = await self.run_command(["pdfinfo", str(p)], principal_id=principal_id)
            if r5.success:
                parts.append(f"pdfinfo:\n{r5.stdout[:2000]}")
            r6 = await self.run_command(
                ["pdftotext", "-l", str(pages), str(p), "-"], principal_id=principal_id
            )
            if r6.success and r6.stdout.strip():
                text = r6.stdout.strip()[:50000]
                parts.append(f"pdftotext:\n{text[:4000]}")
                evidence.append(
                    ExtractionEvidence(
                        kind="pdf_text",
                        processor="pdftotext",
                        payload={"text": text, "max_pages": pages},
                        quality_status="usable" if len(text) > 40 else "uncertain",
                        span={"max_pages": pages, "page_count": probe.page_count},
                        provenance={"path": str(p.resolve())},
                    ).to_dict()
                )
            else:
                evidence.append(
                    ExtractionEvidence(
                        kind="pdf_text",
                        processor="pdftotext",
                        payload={"text": "", "max_pages": pages},
                        quality_status="unusable",
                        warnings=["pdf_text_empty", "may_be_scanned"],
                        span={"max_pages": pages, "page_count": probe.page_count},
                        provenance={"path": str(p.resolve())},
                    ).to_dict()
                )

        # Video/audio: probe only here (no auto STT)
        if probe.kind in ("video", "audio") and probe.raw_probe.get("ffprobe"):
            parts.append("ffprobe: available (see structured.probe)")

        structured = {
            "probe": probe.to_dict(),
            "evidence": evidence,
            "note": (
                "Capabilities listed are available for this asset. "
                "Call transcribe_audio, describe_image, extract_video_audio, "
                "or extract_video_frames when you need those outputs. "
                "Do not assume extraction was performed unless evidence is present."
            ),
        }
        return TerminalResult(
            True,
            "\n\n".join(parts),
            "",
            0,
            0,
            cwd=str(p.parent),
            structured=structured,
        )


def get_terminal() -> TerminalExecutor:
    return TerminalExecutor()
