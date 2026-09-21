"""Single authoritative webhook signature path (source-level + pure unit)."""

from pathlib import Path
import hashlib
import hmac

from wax.security.webhooks import verify_whatsapp_signature, verify_telegram_secret


def test_verify_whatsapp_empty_secret_not_required():
    assert verify_whatsapp_signature("", b"body", None, required=False) is True
    assert verify_whatsapp_signature("", b"body", None, required=True) is False


def test_verify_whatsapp_valid_hmac():
    secret = "test-secret"
    body = b'{"ok":true}'
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_whatsapp_signature(secret, body, sig, required=True) is True
    assert verify_whatsapp_signature(secret, body, "sha256=deadbeef", required=True) is False


def test_whatsapp_handler_uses_shared_helper():
    src = Path("wax/messaging/whatsapp/handler.py").read_text()
    assert "from wax.security.webhooks import verify_whatsapp_signature" in src
    assert "hmac.new" not in src


def test_telegram_handler_uses_shared_secret_helper():
    src = Path("wax/messaging/telegram/handler.py").read_text()
    assert "verify_telegram_secret" in src


def test_telegram_secret_timing_safe():
    assert verify_telegram_secret("abc", "abc") is True
    assert verify_telegram_secret("abc", "abd") is False
    assert verify_telegram_secret(None, None) is True
