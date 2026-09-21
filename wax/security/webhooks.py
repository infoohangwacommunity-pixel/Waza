"""
Webhook security helpers — single authoritative signature / secret verification.

Channel handlers must import from here. Do not re-implement HMAC in adapters.
"""

from __future__ import annotations

import hashlib
import hmac


def verify_whatsapp_signature(
    app_secret: str | None,
    body: bytes,
    signature_header: str | None,
    *,
    required: bool = True,
) -> bool:
    """
    Verify X-Hub-Signature-256 (sha256=<hex>).

    When app_secret is empty:
      - required=False → allow (local dev)
      - required=True  → reject
    """
    if not app_secret:
        return not required
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        app_secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    received = signature_header[7:]
    return hmac.compare_digest(expected, received)


def verify_telegram_secret(expected: str | None, header_value: str | None) -> bool:
    """Timing-safe compare of X-Telegram-Bot-Api-Secret-Token."""
    if not expected:
        return True
    if not header_value:
        return False
    return hmac.compare_digest(expected, header_value)
