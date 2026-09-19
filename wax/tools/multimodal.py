"""
Optional multimodal inspection — only when the tutor explicitly asks.

Default path remains local terminal (OCR/ffprobe/pdf).
This is a deliberate fallback, not the first choice.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx

from wax.config import get_settings
from wax.observability.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


async def describe_local_image(path: str, question: str | None = None) -> dict[str, Any]:
    """
    Send a local image to a multimodal model if configured.
    Prefer inspect_media (OCR) first in the tutor flow.
    """
    if settings.multimodal_provider in ("none", "", "null") and not settings.multimodal_api_key:
        # fall back to primary if it looks multimodal-capable and key exists
        api_key = settings.primary_api_key
        base_url = settings.primary_base_url or "https://api.openai.com/v1"
        model = settings.primary_model
        if not api_key:
            return {"ok": False, "error": "multimodal_not_configured"}
    else:
        api_key = settings.multimodal_api_key or settings.primary_api_key
        base_url = (settings.multimodal_base_url or settings.primary_base_url or "https://api.openai.com/v1").rstrip("/")
        model = settings.multimodal_model or settings.primary_model

    p = Path(path)
    if not p.is_file():
        return {"ok": False, "error": "file_not_found"}
    if p.stat().st_size > 8_000_000:
        return {"ok": False, "error": "file_too_large_for_multimodal"}

    suffix = p.suffix.lower()
    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(suffix)
    if not mime:
        return {"ok": False, "error": f"unsupported_image_type:{suffix}"}

    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    prompt = question or (
        "Describe what is in this image clearly. If it is a question, diagram, "
        "homework, or text, extract the useful content for tutoring."
    )
    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                ],
            }
        ],
        "max_tokens": 1200,
        "temperature": 0.2,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(f"{base_url}/chat/completions", headers=headers, json=body)
        if resp.status_code >= 400:
            return {"ok": False, "error": f"http_{resp.status_code}", "body": resp.text[:300]}
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        return {"ok": True, "description": text, "model": model, "path": str(p)}
    except Exception as e:
        logger.exception("multimodal_failed")
        return {"ok": False, "error": str(e)}
