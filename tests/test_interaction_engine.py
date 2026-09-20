"""P0 interaction engine: durable present → consume once → expire."""

from pathlib import Path


def test_interaction_model_exists():
    src = Path("wax/db/models.py").read_text()
    assert "class Interaction(" in src
    assert "callback_token" in src
    assert "consumed_at" in src


def test_migration_008_revises_007():
    src = Path("alembic/versions/008_interactions.py").read_text()
    assert 'revision: str = "008"' in src
    assert 'down_revision' in src and "007" in src


def test_present_choices_creates_interaction():
    src = Path("wax/tools/registry.py").read_text()
    assert "InteractionService" in src
    assert "expires_in_seconds" in src
    assert "get_current_time" in src


def test_telegram_callback_handler_exists():
    src = Path("wax/messaging/telegram/handler.py").read_text()
    assert "_handle_telegram_callback" in src
    assert "callback_query" in src


def test_recovery_expires_interactions():
    src = Path("wax/workers/main.py").read_text()
    assert "expire_due" in src
    assert "interaction_timeout" in src or "timeout" in src


def test_consume_ordering_in_service():
    src = Path("wax/interaction/service.py").read_text()
    assert "already_consumed" in src
    assert "interactive_replay_rejected" in src
    assert "with_for_update" in src or "status == \"pending\"" in src
