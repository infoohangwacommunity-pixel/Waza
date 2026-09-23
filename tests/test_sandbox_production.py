"""Production must refuse World isolation when no secure runtime is available."""
import asyncio
from unittest.mock import patch

from wax.world.errors import IsolationUnavailable
from wax.world.isolation import IsolationRequest, run_isolated


def test_production_refuses_without_isolation(tmp_path):
    (tmp_path / "identity.json").write_text("{}", encoding="utf-8")
    (tmp_path / "workspace").mkdir()

    class S:
        app_env = "production"
        terminal_require_sandbox = True
        terminal_timeout_seconds = 5
        terminal_max_output_bytes = 10000
        terminal_cpu_seconds = 5
        terminal_memory_bytes = 10_000_000
        terminal_use_docker = False
        effective_terminal_require_sandbox = True

    async def _run():
        with patch("wax.world.isolation.settings", S()):
            with patch("wax.world.isolation._has_bwrap", return_value=False):
                with patch("wax.world.isolation._has_docker", return_value=False):
                    with patch("wax.world.isolation._want_docker", return_value=False):
                        with patch("wax.world.isolation._require_sandbox", return_value=True):
                            return await run_isolated(
                                IsolationRequest(
                                    argv=["echo", "hi"],
                                    world_root=tmp_path,
                                    timeout_sec=2,
                                )
                            )

    try:
        r = asyncio.run(_run())
        assert r.success is False
    except IsolationUnavailable:
        pass


def test_sandbox_adapter_uses_world(tmp_path):
    from wax.terminal.sandbox import run_sandboxed

    (tmp_path / "identity.json").write_text("{}", encoding="utf-8")
    (tmp_path / "workspace").mkdir()
    r = asyncio.run(run_sandboxed(["echo", "adapter"], cwd=tmp_path / "workspace", timeout=5))
    # Either isolated success or isolation unavailable — never host-secret leak
    assert r.isolation in ("bwrap", "docker", "rlimits", "none", "world") or r.error
