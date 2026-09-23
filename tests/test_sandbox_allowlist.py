"""Isolation is the boundary — no semantic binary allowlist."""
import asyncio
from pathlib import Path
from unittest.mock import patch

from wax.terminal.sandbox import run_sandboxed, ALLOWED_BINARIES


def test_allowlist_is_empty():
    assert ALLOWED_BINARIES == set() or len(ALLOWED_BINARIES) == 0


def test_production_still_requires_isolation(tmp_path):
    class S:
        app_env = "production"
        terminal_require_sandbox = True
        terminal_timeout_seconds = 5
        terminal_max_output_bytes = 10000
        terminal_cpu_seconds = 5
        terminal_memory_bytes = 10_000_000
        terminal_use_docker = False
        effective_terminal_require_sandbox = True

    with patch("wax.terminal.sandbox.settings", S()):
        with patch("wax.terminal.sandbox._has_bwrap", return_value=False):
            with patch("wax.terminal.sandbox._has_docker", return_value=False):
                with patch("wax.terminal.sandbox._want_docker", return_value=False):
                    r = asyncio.run(run_sandboxed(["echo", "hi"], cwd=tmp_path))
    assert r.success is False
    assert "sandbox_required" in (r.error or "").lower() or "unavailable" in (r.error or "").lower()
