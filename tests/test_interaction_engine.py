"""Interaction present → consume once → expire (source invariants)."""

from pathlib import Path
import ast


def test_interaction_model_exists():
    src = Path("wax/db/models.py").read_text()
    assert "class Interaction(" in src
    assert "callback_token" in src
    assert "consumed_at" in src


def test_telegram_callback_handler_defined():
    path = Path("wax/messaging/telegram/handler.py")
    src = path.read_text()
    assert "callback_query" in src
    tree = ast.parse(src)
    names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "_handle_telegram_callback" in names
    assert "InteractionService" in src
