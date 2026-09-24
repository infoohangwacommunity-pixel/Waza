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


def deterministic_token(*parts: str, nbytes: int = 32) -> str:
    """Re-derivable opaque token from stable parts (idempotency recovery).

    Not a password hash — HMAC of joined parts under the surface token secret.
    Same inputs always yield the same url-safe token.
    """
    import base64
    msg = "|".join(str(p) for p in parts).encode("utf-8")
    dig = hmac.new(_secret(), msg, hashlib.sha256).digest()
    # urlsafe, no padding; truncate to nbytes*4/3-ish chars
    return base64.urlsafe_b64encode(dig).decode("ascii").rstrip("=")[: max(32, nbytes + 8)]
