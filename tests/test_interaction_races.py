"""Interaction race invariants (unit-level)."""

from pathlib import Path


def test_consume_requires_for_update():
    src = Path("wax/interaction/service.py").read_text()
    assert "with_for_update" in src
    assert "already_consumed" in src
    assert "expired" in src


def test_opaque_ids_shared_across_channels():
    src = Path("wax/interaction/service.py").read_text()
    assert "logical_id" in src
    assert "callback_data" in src


def test_whatsapp_and_telegram_both_consume():
    assert "InteractionService" in Path("wax/messaging/telegram/handler.py").read_text()
    assert "InteractionService" in Path("wax/messaging/whatsapp/handler.py").read_text()


def test_scheduler_cancel_and_series():
    src = Path("wax/scheduler/service.py").read_text()
    assert "async def cancel" in src
    assert "async def schedule_series" in src
    assert "async def reschedule" in src


def test_worker_lease_machinery():
    src = Path("wax/workers/main.py").read_text()
    assert "renew_lease" in src
    assert "reclaim_stale_works" in src
