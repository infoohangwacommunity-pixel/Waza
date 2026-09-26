
"""Regression: process_message_response must not shadow module-level datetime."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "wax/workers/main.py").read_text()


def test_no_local_datetime_import_in_process_message_response():
    tree = ast.parse(SRC)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "process_message_response":
            for child in ast.walk(node):
                if isinstance(child, ast.ImportFrom) and child.module == "datetime":
                    raise AssertionError(
                        f"local datetime import at line {child.lineno} shadows module datetime"
                    )
            # Must still *use* datetime in completion/retry paths
            body = ast.get_source_segment(SRC, node) or ""
            assert "datetime.now" in body
            return
    raise AssertionError("process_message_response not found")


def test_module_imports_datetime():
    assert "from datetime import datetime, timedelta, timezone" in SRC
