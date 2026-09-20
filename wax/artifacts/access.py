"""Signed artifact download tokens — ownership-bound, expiring, no path leakage."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from urllib.parse import quote

from wax.config import get_settings


def _secret() -> bytes:
    s = get_settings()
    raw = (os.environ.get("WAX_ENCRYPTION_KEY") or s.secret_key or "dev").encode()
    return hashlib.sha256(raw).digest()


def make_download_token(artifact_id: str, principal_id: str, ttl_seconds: int = 86400 * 7) -> str:
    exp = int(time.time()) + max(60, ttl_seconds)
    msg = f"{artifact_id}:{principal_id}:{exp}".encode()
    sig = hmac.new(_secret(), msg, hashlib.sha256).hexdigest()[:32]
    return f"{exp}.{sig}"


def verify_download_token(artifact_id: str, principal_id: str, token: str) -> bool:
    try:
        exp_s, sig = token.split(".", 1)
        exp = int(exp_s)
    except Exception:
        return False
    if exp < int(time.time()):
        return False
    msg = f"{artifact_id}:{principal_id}:{exp}".encode()
    expected = hmac.new(_secret(), msg, hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(expected, sig)


def public_base_url() -> str:
    return (
        os.environ.get("PUBLIC_BASE_URL")
        or os.environ.get("WAX_PUBLIC_BASE_URL")
        or ""
    ).rstrip("/")


def public_download_url(artifact_id: str, token: str) -> str | None:
    base = public_base_url()
    if not base:
        return None
    return f"{base}/artifacts/{quote(artifact_id)}/download?token={quote(token)}"
