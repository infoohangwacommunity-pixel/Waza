"""World workspace staging."""

from pathlib import Path

from wax.world.stage import principal_workspace, safe_write_bytes, content_hash
from wax.world.layout import workspace_root


def test_workspace_root_exists():
    root = workspace_root()
    assert root.exists()


def test_principal_workspace_creates_dirs():
    p = principal_workspace("test-user-1")
    assert p.exists()
    assert (p / "media").is_dir()
    assert (p / "projects").is_dir()


def test_safe_write_and_hash():
    p = principal_workspace("test-user-2")
    dest = safe_write_bytes(p / "media", "hello.txt", b"hello wax")
    assert dest.exists()
    assert content_hash(b"hello wax") == content_hash(dest.read_bytes())
