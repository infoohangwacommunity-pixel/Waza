"""
Embedding service for semantic memory retrieval.

Configured independently of the chat provider via EMBEDDING_* env vars.
OpenAI-compatible POST {base}/embeddings (Voyage, OpenAI, etc.).

Falls back gracefully — structured hybrid retrieval still works without vectors.
"""

from __future__ import annotations

import math
import os
from typing import Any

import httpx

from wax.config import get_settings
from wax.observability.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

# Convenience defaults when operator sets PROVIDER name but omits BASE_URL.
# Not educational hardcoding — infrastructure endpoint hints only.
_PROVIDER_BASE_HINTS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "voyage": "https://api.voyageai.com/v1",
    "voyageai": "https://api.voyageai.com/v1",
}


def _embedding_config() -> tuple[str, str, str] | None:
    """
    Return (api_key, base_url, model) for embeddings.

    Priority:
    1. Explicit EMBEDDING_API_KEY (+ optional base/model/provider)
    2. Else, if embedding_provider is none/empty, no dedicated embeddings
       (do NOT silently send embedding traffic to the chat provider —
        that caused Cerebras /embeddings 402 noise)
    """
    key = (settings.embedding_api_key or os.environ.get("EMBEDDING_API_KEY") or "").strip()
    provider = (
        settings.embedding_provider
        or os.environ.get("EMBEDDING_PROVIDER")
        or "none"
    ).strip().lower()
    base = (
        settings.embedding_base_url
        or os.environ.get("EMBEDDING_BASE_URL")
        or ""
    ).strip().rstrip("/")
    model = (
        settings.embedding_model
        or os.environ.get("EMBEDDING_MODEL")
        or "text-embedding-3-small"
    ).strip()

    if not key:
        # No dedicated embedding key → skip vectors (retrieval still works)
        return None

    if not base:
        if provider in _PROVIDER_BASE_HINTS:
            base = _PROVIDER_BASE_HINTS[provider]
        elif provider in ("none", "", "null"):
            # Key set without provider/base — refuse to guess chat provider URL
            logger.warning(
                "embedding_base_url_missing",
                hint="Set EMBEDDING_BASE_URL (e.g. https://api.voyageai.com/v1) "
                "or EMBEDDING_PROVIDER=voyage",
            )
            return None
        else:
            logger.warning(
                "embedding_base_url_unknown_provider",
                provider=provider,
                hint="Set EMBEDDING_BASE_URL explicitly",
            )
            return None

    return key, base, model


async def embed_texts(texts: list[str]) -> list[list[float]] | None:
    cfg = _embedding_config()
    if not cfg or not texts:
        return None
    api_key, base, model = cfg
    cleaned = [(t or "")[:6000] for t in texts]
    url = f"{base}/embeddings"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": model, "input": cleaned},
            )
        if resp.status_code >= 400:
            logger.warning(
                "embed_failed",
                status=resp.status_code,
                url=url,
                model=model,
                body=resp.text[:200],
            )
            return None
        data = resp.json()
        items = sorted(data.get("data") or [], key=lambda x: x.get("index", 0))
        vectors = [it.get("embedding") or [] for it in items]
        if vectors:
            logger.info(
                "embed_ok",
                model=model,
                count=len(vectors),
                dims=len(vectors[0]) if vectors[0] else 0,
            )
        return vectors
    except Exception as e:
        logger.warning("embed_error", error=str(e), url=url)
        return None


async def embed_one(text: str) -> list[float] | None:
    result = await embed_texts([text])
    if not result:
        return None
    return result[0] or None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))
