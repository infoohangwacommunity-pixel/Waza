"""
Encrypt sensitive values at rest with Fernet when cryptography is available.

Never put secrets into model context, logs, terminal env, or API responses.
"""

from __future__ import annotations

import base64
import hashlib
import os
from functools import lru_cache

from wax.config import get_settings
from wax.observability.logging import get_logger

logger = get_logger(__name__)


@lru_cache
def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return None

    raw = os.environ.get("WAX_ENCRYPTION_KEY") or get_settings().secret_key or "dev-only-key"
    # Derive a stable 32-byte url-safe base64 key from the configured secret
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_str(plaintext: str) -> str:
    if not plaintext:
        return ""
    f = _fernet()
    if f is not None:
        token = f.encrypt(plaintext.encode("utf-8")).decode("ascii")
        return "fern:" + token
    # Fallback XOR (dev only) — still never log plaintext
    key = hashlib.sha256(
        (os.environ.get("WAX_ENCRYPTION_KEY") or get_settings().secret_key or "dev").encode()
    ).digest()
    data = plaintext.encode("utf-8")
    out = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    return "wax1:" + base64.urlsafe_b64encode(out).decode("ascii")


def decrypt_str(token: str) -> str:
    if not token:
        return ""
    if token.startswith("fern:"):
        f = _fernet()
        if f is None:
            raise RuntimeError("cryptography required to decrypt Fernet secrets")
        return f.decrypt(token[5:].encode("ascii")).decode("utf-8")
    if token.startswith("wax1:"):
        key = hashlib.sha256(
            (os.environ.get("WAX_ENCRYPTION_KEY") or get_settings().secret_key or "dev").encode()
        ).digest()
        raw = base64.urlsafe_b64decode(token[5:].encode("ascii"))
        out = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
        return out.decode("utf-8", errors="replace")
    return token  # legacy plaintext
