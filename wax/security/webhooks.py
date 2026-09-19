"""
Webhook security helpers — signature verification, timing-safe compares.
"""

from __future__ import annotations

import hashlib
import hmac


def verify_whatsapp_signature(app_secret: str, body: bytes, signature_header: str | None) -> bool:
    if not app_secret:
        return True  # development only when secret unset
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    received = signature_header[7:]
    return hmac.compare_digest(expected, received)


def verify_telegram_secret(expected: str | None, header_value: str | None) -> bool:
    if not expected:
        return True
    if not header_value:
        return False
    return hmac.compare_digest(expected, header_value)
