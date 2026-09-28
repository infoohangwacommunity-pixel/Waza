Static checks: models FK ownership and single baseline.

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def test_single_baseline_file():
    versions = list((ROOT / "alembic/versions").glob("*.py"))
    assert len(versions) == 1
    src = versions[0].read_text()
    assert "001_reality" in src
    assert "down_revision" in src


def test_no_activities_fk():
    src = (ROOT / "wax/db/models.py").read_text()
    assert "activities.id" not in src
    assert "memory_episodes" not in src


def test_principal_owned_use_cascade():
    src = (ROOT / "wax/db/models.py").read_text()
    for m in re.finditer(
        r"principal_id:.*?ForeignKey\(\"principals\.id\", ondelete=\"([^\"]+)\"\)",
        src,
        re.DOTALL,
    ):
        assert m.group(1) == "CASCADE", m.group(0)[:120]


def test_verify_schema_head():
    src = (ROOT / "scripts/verify_schema.py").read_text()
    assert "001_reality" in src
    assert "FORBIDDEN_TABLES" in src
