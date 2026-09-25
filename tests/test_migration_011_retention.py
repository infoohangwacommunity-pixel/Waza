"""Regression: migration 011 retention columns must not use broken 3-tuple unpack."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIG011 = ROOT / "alembic/versions/011_learner_intelligence.py"
MIG012 = ROOT / "alembic/versions/012_learner_intelligence_cleanup.py"


def test_011_parses_as_python():
    src = MIG011.read_text()
    ast.parse(src)


def test_011_no_two_var_unpack_of_three_tuples():
    src = MIG011.read_text()
    # The exact production failure: for col, typ in [("stability", sa.Float(), "1.0"), ...]
    assert "for col, typ in" not in src
    assert "typ is sa.Float()" not in src
    assert "except Exception" not in src


def test_011_retention_columns_are_explicit_sql():
    src = MIG011.read_text()
    required = [
        "stability DOUBLE PRECISION DEFAULT 1.0",
        "difficulty DOUBLE PRECISION DEFAULT 0.3",
        "retrievability DOUBLE PRECISION",
        "lapse_count INTEGER DEFAULT 0",
        "independent_successes INTEGER DEFAULT 0",
        "assisted_successes INTEGER DEFAULT 0",
        "review_count INTEGER DEFAULT 0",
    ]
    for frag in required:
        assert frag in src, f"missing {frag}"
    assert "ADD COLUMN IF NOT EXISTS" in src


def test_012_is_idempotent_safety_net():
    src = MIG012.read_text()
    assert "ADD COLUMN IF NOT EXISTS stability" in src
    assert "except Exception" not in src


def test_migration_graph_single_head():
    versions = ROOT / "alembic/versions"
    revs = {}
    for f in sorted(versions.glob("*.py")):
        text = f.read_text()
        m = re.search(r'^revision:\s*str\s*=\s*["\']([^"\']+)', text, re.M)
        d = re.search(r'^down_revision:\s*(?:Union\[[^\]]+\]\s*)?=\s*(None|["\'][^"\']+["\'])', text, re.M)
        rev = m.group(1) if m else None
        down = None if not d or d.group(1) == "None" else d.group(1).strip("'\"")
        assert rev, f"no revision in {f.name}"
        revs[rev] = down
    for rev, down in revs.items():
        if down is not None:
            assert down in revs, f"{rev} missing parent {down}"
    children = set(d for d in revs.values() if d)
    heads = [r for r in revs if r not in children]
    assert heads == ["017_principal_workloads"], heads


def test_013_creates_publications_017_creates_principal_workloads():
    assert "publications" in (ROOT / "alembic/versions/013_publications.py").read_text()
    assert "principal_workloads" in (ROOT / "alembic/versions/017_principal_workloads.py").read_text()
