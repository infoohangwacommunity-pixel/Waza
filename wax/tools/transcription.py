"""
Audio transcription — modality, not a teaching mode.

Order:
1. OpenAI-compatible /audio/transcriptions when API key is configured
2. Optional local whisper CLI if present in sandbox allowlist later

Never pretends success. Caller must surface ok=false to the model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from wax.config import get_settings
from wax.observability.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

AUDIO_SUFFIXES = {".ogg", ".oga", ".mp3", ".m4a", ".wav", ".webm", ".mp4", ".mpeg", ".mpga", ".flac"}


async def transcribe_local_audio(path: str, *, language: str | None = None) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        return {"ok": False, "error": "file_not_found", "path": path}
    if p.suffix.lower() not in AUDIO_SUFFIXES and "audio" not in (p.name.lower()):
        # Still try — Telegram voice notes may be .ogg without clear mime in name
        if p.suffix.lower() not in AUDIO_SUFFIXES:
            logger.info("transcribe_unusual_suffix", path=path, suffix=p.suffix)

    api_key = settings.primary_api_key or ""
    base_url = (settings.primary_base_url or "https://api.openai.com/v1").rstrip("/")
    if not api_key:
        return {
            "ok": False,
            "error": "transcription_not_configured",
            "hint": "Set PRIMARY_API_KEY for OpenAI-compatible /audio/transcriptions",
        }

    model = getattr(settings, "transcription_model", None) or "whisper-1"
    data = {
        "model": model,
        "response_format": "json",
    }
    if language:
        data["language"] = language[:16]

    mime = {
        ".ogg": "audio/ogg",
        ".oga": "audio/ogg",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".wav": "audio/wav",
        ".webm": "audio/webm",
        ".mp4": "audio/mp4",
        ".flac": "audio/flac",
    }.get(p.suffix.lower(), "application/octet-stream")

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            with p.open("rb") as f:
                files = {"file": (p.name, f, mime)}
                resp = await client.post(
                    f"{base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    data=data,
                    files=files,
                )
        if resp.status_code >= 400:
            logger.warning(
                "transcription_api_failed",
                status=resp.status_code,
                body=resp.text[:300],
            )
            return {
                "ok": False,
                "error": "transcription_api_failed",
                "status_code": resp.status_code,
                "detail": resp.text[:300],
            }
        body = resp.json()
        text = (body.get("text") or "").strip()
        if not text:
            return {"ok": False, "error": "empty_transcript"}
        return {
            "ok": True,
            "transcript": text[:20000],
            "path": str(p),
            "model": model,
            "provider": "openai_compatible",
        }
    except Exception as e:
        logger.exception("transcription_error")
        return {"ok": False, "error": "transcription_exception", "detail": str(e)[:300]}
