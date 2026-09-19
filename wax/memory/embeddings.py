"""
Embedding service for semantic memory retrieval.

Uses OpenAI-compatible /embeddings endpoints when configured.
Falls back gracefully — structured hybrid retrieval still works without vectors.
"""

from __future__ import annotations

import math
from typing import Any

import httpx

from wax.config import get_settings
from wax.observability.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


def _embedding_config() -> tuple[str, str, str] | None:
    """api_key, base_url, model"""
    key = settings.primary_api_key
    if not key:
        return None
    base = (settings.primary_base_url or "https://api.openai.com/v1").rstrip("/")
    # Allow override via model name heuristic; default text-embedding-3-small
    model = getattr(settings, "embedding_model", None) or "text-embedding-3-small"
    return key, base, model


async def embed_texts(texts: list[str]) -> list[list[float]] | None:
    cfg = _embedding_config()
    if not cfg or not texts:
        return None
    api_key, base, model = cfg
    # truncate very long inputs
    cleaned = [(t or "")[:6000] for t in texts]
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{base}/embeddings",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": model, "input": cleaned},
            )
        if resp.status_code >= 400:
            logger.warning("embed_failed", status=resp.status_code, body=resp.text[:200])
            return None
        data = resp.json()
        items = sorted(data.get("data") or [], key=lambda x: x.get("index", 0))
        return [it.get("embedding") or [] for it in items]
    except Exception as e:
        logger.warning("embed_error", error=str(e))
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
