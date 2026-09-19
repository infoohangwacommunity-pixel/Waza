"""Smoke: empty Postgres can reach Alembic HEAD with required tables."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest


REQUIRED = ("works", "inbound_events", "principals", "messages", "deliveries")


@pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="Set TEST_DATABASE_URL to a disposable empty Postgres DSN",
)
def test_alembic_upgrade_head_creates_core_tables():
    url = os.environ["TEST_DATABASE_URL"]
    env = {**os.environ, "DATABASE_URL": url}
    r = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env,
        capture_output=True,
        text=True,
        cwd=os.path.dirname(os.path.dirname(__file__)),
    )
    assert r.returncode == 0, f"alembic failed:\n{r.stdout}\n{r.stderr}"

    # verify via verify_schema script
    v = subprocess.run(
        [sys.executable, "scripts/verify_schema.py"],
        env=env,
        capture_output=True,
        text=True,
        cwd=os.path.dirname(os.path.dirname(__file__)),
    )
    assert v.returncode == 0, f"verify_schema failed:\n{v.stdout}\n{v.stderr}"
    assert "works" in v.stdout
