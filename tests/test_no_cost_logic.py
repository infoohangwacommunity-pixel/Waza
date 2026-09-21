"""Guardrail: no educational hardcoding or cost-gating in intelligence layer.

Matches real program constructs (class/def/assignment names), not comment substrings.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

# Names that must never appear as identifiers / class names / function names in wax/
FORBIDDEN_IDENTIFIERS = {
    "CostProtector",
    "cost_exceeded",
    "can_spend",
    "estimate_llm_cost",
    "budget_cents",
    "JAMBMode",
    "QuizMode",
    "StudentMode",
    "ExamMode",
    "ChemistryTutor",
    "BiologyTutor",
    "PhysicsTutor",
    "AnatomyMode",
}


def _collect_identifiers(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.keyword):
            if node.arg:
                names.add(node.arg)
        elif isinstance(node, ast.alias):
            names.add(node.name)
            if node.asname:
                names.add(node.asname)
    return names


def test_no_forbidden_patterns():
    root = Path("wax")
    hits: list[str] = []
    for path in sorted(root.rglob("*.py")):
        text = path.read_text(errors="ignore")
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError as e:
            hits.append(f"{path}:SYNTAX:{e}")
            continue
        ids = _collect_identifiers(tree)
        for forbidden in FORBIDDEN_IDENTIFIERS:
            if forbidden in ids:
                hits.append(f"{path}:{forbidden}")
    assert not hits, hits


def test_no_forbidden_string_assignments():
    """Catch mode = 'QuizMode' style assignments without relying on comments."""
    root = Path("wax")
    pattern = re.compile(
        r"""(?:mode|tutor_mode|exam_mode|quiz_mode)\s*=\s*['"](?:QuizMode|ExamMode|JAMBMode|StudentMode)['"]""",
        re.IGNORECASE,
    )
    hits = []
    for path in sorted(root.rglob("*.py")):
        text = path.read_text(errors="ignore")
        lines = []
        for line in text.splitlines():
            stripped = line.split("#", 1)[0]
            lines.append(stripped)
        joined = "\n".join(lines)
        if pattern.search(joined):
            hits.append(str(path))
    assert not hits, hits
