"""Webhook handlers must not call media download or typing inside the module at import time."""
import inspect
from wax.messaging.whatsapp import handler as wh
from wax.messaging.telegram import handler as th

def test_whatsapp_handler_source_has_no_media_fetch_call():
    src = inspect.getsource(wh.handle_whatsapp_webhook)
    assert "fetch_whatsapp_media" not in src
    assert "send_typing" not in src

def test_telegram_handler_source_has_no_media_fetch_call():
    src = inspect.getsource(th.handle_telegram_webhook)
    assert "fetch_telegram_media" not in src
    assert "send_typing" not in src
