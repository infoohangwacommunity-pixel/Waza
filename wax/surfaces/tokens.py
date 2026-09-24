"""Capability tokens for surfaces — opaque, scoped, hashed at rest. Never log raw tokens."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets


DEFAULT_TOKEN_BYTES = 32


def _secret() -> bytes:
    raw = (
        os.environ.get("WAX_SURFACE_TOKEN_SECRET")
        or os.environ.get("WAX_PUBLICATION_TOKEN_SECRET")
        or os.environ.get("WAX_ENCRYPTION_KEY")
        or os.environ.get("SECRET_KEY")
        or "dev-insecure"
    ).encode()
    try:
        from wax.config import get_settings
        s = get_settings()
        if not os.environ.get("WAX_SURFACE_TOKEN_SECRET") and getattr(s, "secret_key", None):
            raw = (os.environ.get("WAX_ENCRYPTION_KEY") or s.secret_key or "dev-insecure").encode()
    except Exception:
        pass
    return hashlib.sha256(raw).digest()


def generate_token(nbytes: int = DEFAULT_TOKEN_BYTES) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    return hmac.new(_secret(), token.encode("utf-8"), hashlib.sha256).hexdigest()


def tokens_match(presented: str, stored_hash: str) -> bool:
    if not presented or not stored_hash or len(presented) < 16:
        return False
    return hmac.compare_digest(hash_token(presented), stored_hash)


def scope_set(*scopes: str) -> list[str]:
    return sorted(set(scopes))
