"""Web start must not pass a literal $PORT to uvicorn."""

from pathlib import Path


def test_start_web_resolves_port_from_env():
    src = Path("scripts/start_web.sh").read_text()
    assert 'PORT="${PORT:-8080}"' in src
    assert 'uvicorn' in src
    assert '"${PORT}"' in src or "'${PORT}'" in src or "${PORT}" in src


def test_railway_start_command_has_no_dollar_port():
    src = Path("railway.toml").read_text()
    assert "start_web.sh" in src
    # The startCommand line itself must not contain $PORT
    for line in src.splitlines():
        if "startCommand" in line:
            assert "$PORT" not in line, line


def test_procfile_web_uses_start_web():
    assert "start_web.sh" in Path("Procfile").read_text()
