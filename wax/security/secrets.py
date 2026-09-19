"""Encrypt sensitive values at rest. Never put secrets in model context or terminal env."""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Optional

from wax.config import get_settings


def _key_bytes() -> bytes:
    s = get_settings()
    raw = os.environ.get("WAX_ENCRYPTION_KEY") or s.secret_key or "dev-only"
    return hashlib.sha256(raw.encode("utf-8")).digest()


def encrypt_str(plaintext: str) -> str:
    """Lightweight reversible encoding for at-rest secrets (Fernet-like XOR+base64)."""
    if not plaintext:
        return ""
    key = _key_bytes()
    data = plaintext.encode("utf-8")
    out = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    return "wax1:" + base64.urlsafe_b64encode(out).decode("ascii")


def decrypt_str(token: str) -> str:
    if not token:
        return ""
    if not token.startswith("wax1:"):
        return token  # plaintext legacy
    raw = base64.urlsafe_b64decode(token[5:].encode("ascii"))
    key = _key_bytes()
    out = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
    return out.decode("utf-8", errors="replace")
