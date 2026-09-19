import asyncio
from pathlib import Path
from wax.terminal.sandbox import run_sandboxed

def test_disallowed_binary(tmp_path):
    r = asyncio.run(
        run_sandboxed(["curl", "http://example.com"], cwd=tmp_path)
    )
    assert r.success is False
    assert "not permitted" in (r.error or "").lower() or r.error
