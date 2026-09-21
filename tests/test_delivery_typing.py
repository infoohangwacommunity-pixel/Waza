"""Typing indicators are wired into the delivery path (not webhook accept)."""

from pathlib import Path


def test_deliver_accepts_show_typing():
    src = Path("wax/delivery/senders.py").read_text()
    assert "async def send_typing" in src
    assert "show_typing" in src
    assert "inbound_message_id" in src


def test_worker_passes_typing_kwargs():
    src = Path("wax/workers/main.py").read_text()
    assert "show_typing" in src
    assert "inbound_message_id" in src


def test_webhook_handlers_still_pure():
    """Typing must not move into webhook accept."""
    wa = Path("wax/messaging/whatsapp/handler.py").read_text()
    tg = Path("wax/messaging/telegram/handler.py").read_text()
    # Accept path must not call channel typing helpers
    assert "send_typing_and_read" not in wa
    assert "send_typing(" not in tg.split("async def _handle_telegram_callback")[0]
