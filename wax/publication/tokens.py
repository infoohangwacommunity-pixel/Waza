"""
Temporary capability tokens for publications.

Opaque, high-entropy, hashed at rest. Never log raw tokens.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timezone

from wax.config import get_settings

# Minimum entropy: 32 bytes → 43-char urlsafe
DEFAULT_TOKEN_BYTES = 32


def _token_secret() -> bytes:
    s = get_settings()
    raw = (
        os.environ.get("WAX_PUBLICATION_TOKEN_SECRET")
        or os.environ.get("WAX_ENCRYPTION_KEY")
        or s.secret_key
        or "dev-insecure"
    ).encode()
    return hashlib.sha256(raw).digest()


def generate_public_token(nbytes: int = DEFAULT_TOKEN_BYTES) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    return hmac.new(_token_secret(), token.encode("utf-8"), hashlib.sha256).hexdigest()


def tokens_match(presented: str, stored_hash: str) -> bool:
    if not presented or not stored_hash:
        return False
    if len(presented) < 16:
        return False
    expected = hash_token(presented)
    return hmac.compare_digest(expected, stored_hash)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
