"""No semantic binary allowlist remains."""

def test_sandbox_module_has_no_allowlist():
    src = open("wax/terminal/sandbox.py", encoding="utf-8").read()
    assert "ALLOWED_BINARIES" not in src
    assert "Command not permitted" not in src
