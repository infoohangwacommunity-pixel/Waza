"""
Cryptographically secure, opaque publication access tokens.

Public URL contains ONLY the token — never principal_id, work_id, or storage paths.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timezone

from wax.config import get_settings


def _token_secret() -> bytes:
    s = get_settings()
    raw = (
        os.environ.get("WAX_PUBLICATION_TOKEN_SECRET")
        or os.environ.get("WAX_ENCRYPTION_KEY")
        or s.secret_key
        or "dev-insecure"
    ).encode()
    return hashlib.sha256(raw).digest()


def generate_public_token(nbytes: int = 32) -> str:
    """URL-safe opaque token (default 256 bits of entropy)."""
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """Store only a keyed hash of the public token."""
    return hmac.new(_token_secret(), token.encode("utf-8"), hashlib.sha256).hexdigest()


def tokens_match(presented: str, stored_hash: str) -> bool:
    if not presented or not stored_hash:
        return False
    expected = hash_token(presented)
    return hmac.compare_digest(expected, stored_hash)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
