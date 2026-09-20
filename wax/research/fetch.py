"""Network research primitives — capability-scoped, not unrestricted browse.

World knowledge from the web must NOT auto-become learner memory.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from wax.observability.logging import get_logger

logger = get_logger(__name__)

# Deny obvious internal/metadata targets
_BLOCKED_HOST_SUFFIXES = (
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "metadata.google.internal",
    "169.254.169.254",
)
_MAX_BYTES = 500_000


def _host_allowed(host: str) -> bool:
    h = (host or "").lower().strip(".")
    if not h:
        return False
    if h in _BLOCKED_HOST_SUFFIXES or h.endswith(".local") or h.endswith(".internal"):
        return False
    # Block private IP literals roughly
    if re.match(r"^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[0-1])\.)", h):
        return False
    return True


async def fetch_url(url: str, *, timeout: float = 20.0) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {"ok": False, "error": "scheme_not_allowed"}
    if not _host_allowed(parsed.hostname or ""):
        return {"ok": False, "error": "host_not_allowed"}
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            max_redirects=5,
            headers={"User-Agent": "WAX-Research/1.0"},
        ) as client:
            resp = await client.get(url)
        data = resp.content[:_MAX_BYTES]
        text = data.decode("utf-8", errors="replace")
        # crude strip tags for summary
        plain = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
        plain = re.sub(r"<style[\s\S]*?</style>", " ", plain, flags=re.I)
        plain = re.sub(r"<[^>]+>", " ", plain)
        plain = re.sub(r"\s+", " ", plain).strip()
        content_hash = hashlib.sha256(data).hexdigest()[:32]
        logger.info(
            "research_fetch_ok",
            host=parsed.hostname,
            status=resp.status_code,
            bytes=len(data),
        )
        return {
            "ok": True,
            "url": str(resp.url),
            "status_code": resp.status_code,
            "title": None,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "content_hash": content_hash,
            "text_preview": plain[:4000],
            "note": "World knowledge only — do not store as learner memory unless the learner owns this content.",
        }
    except Exception as e:
        logger.warning("research_fetch_failed", error=str(e)[:200])
        return {"ok": False, "error": str(e)[:300]}


async def search_stub(query: str) -> dict[str, Any]:
    """Placeholder search — returns structured empty result until a search provider is configured."""
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "empty_query"}
    return {
        "ok": True,
        "query": q,
        "results": [],
        "note": "No search provider configured. Use research_fetch with a specific URL, or configure a search API later.",
    }


async def search_web(query: str, *, max_results: int = 5) -> dict[str, Any]:
    """Optional search provider via WAX_SEARCH_URL (GET ?q=). Falls back to stub."""
    import os
    base = (os.environ.get("WAX_SEARCH_URL") or "").strip()
    if not base:
        return await search_stub(query)
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "empty_query"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(base, params={"q": q, "limit": max_results})
        if resp.status_code >= 400:
            return {"ok": False, "error": f"search_http_{resp.status_code}"}
        data = resp.json()
        results = data if isinstance(data, list) else data.get("results") or data.get("items") or []
        cleaned = []
        for r in results[:max_results]:
            if isinstance(r, dict):
                cleaned.append(
                    {
                        "title": r.get("title") or r.get("name"),
                        "url": r.get("url") or r.get("link"),
                        "snippet": (r.get("snippet") or r.get("description") or "")[:500],
                    }
                )
        logger.info("research_search_ok", n=len(cleaned))
        return {
            "ok": True,
            "query": q,
            "results": cleaned,
            "note": "World knowledge only — do not auto-write as learner memory.",
        }
    except Exception as e:
        logger.warning("research_search_failed", error=str(e)[:200])
        return {"ok": False, "error": str(e)[:300]}
