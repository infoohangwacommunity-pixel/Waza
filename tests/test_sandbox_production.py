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
        isolation_require_sandbox = True
        isolation_timeout_seconds = 5
        isolation_max_output_bytes = 10000
        isolation_cpu_seconds = 5
        isolation_memory_bytes = 10_000_000
        isolation_use_docker = False
        effective_isolation_require_sandbox = True

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
