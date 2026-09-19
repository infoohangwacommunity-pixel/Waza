from pathlib import Path
from wax.terminal.workspace import principal_workspace, safe_write_bytes, list_files, workspace_root


def test_principal_workspace_creates_dirs():
    p = principal_workspace("test-user-1")
    assert p.exists()
    assert (p / "media").is_dir()
    assert (p / "out").is_dir()


def test_safe_write_and_list():
    p = principal_workspace("test-user-2")
    dest = safe_write_bytes(p / "media", "hello.txt", b"hello wax")
    assert dest.exists()
    files = list_files(p / "media")
    assert any(f["path"] == str(dest) for f in files)
