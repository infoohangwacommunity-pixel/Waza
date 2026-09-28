"""No semantic binary allowlist in isolation — security is mounts/namespaces/budgets."""

from pathlib import Path


def test_isolation_module_has_no_allowlist():
    src = Path("wax/world/isolation.py").read_text(encoding="utf-8")
    assert "ALLOWED_BINARIES" not in src
    assert "Command not permitted" not in src
