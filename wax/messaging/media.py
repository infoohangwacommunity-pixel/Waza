"""
Inbound channel media download — infrastructure only.

Download WhatsApp/Telegram media into the student's World workspace.
Does not transcribe, OCR, classify, or interpret content.
The AI decides any processing inside the World.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from wax.config import get_settings
from wax.observability.logging import get_logger
from wax.world.stage import principal_workspace, safe_write_bytes, content_hash

logger = get_logger(__name__)
settings = get_settings()


def _guess_mime(name: str, file_path: str = "") -> str | None:
    """Filename/extension hint only — not content classification for intelligence."""
    lower = (name or "").lower()
    path_l = (file_path or "").lower()
    if lower.endswith((".oga", ".ogg", ".opus")) or "voice" in path_l:
        return "audio/ogg"
    if lower.endswith((".mp3",)):
        return "audio/mpeg"
    if lower.endswith((".m4a", ".aac")):
        return "audio/mp4"
    if lower.endswith((".wav",)):
        return "audio/wav"
    if lower.endswith((".mp4", ".mov", ".mkv", ".m4v")):
        return "video/mp4"
    if lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".gif"):
        return "image/gif"
    if lower.endswith(".pdf"):
        return "application/pdf"
    if lower.endswith((".txt", ".md", ".csv")):
        return "text/plain"
    return None


def _ensure_suffix(name: str, file_path: str = "") -> str:
    """Preserve a recognizable extension for audio voice notes when Telegram omits one."""
    lower = name.lower()
    known = (
        ".oga", ".ogg", ".opus", ".mp3", ".m4a", ".wav", ".webm",
        ".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf",
        ".mp4", ".mov", ".txt", ".doc", ".docx",
    )
    if any(lower.endswith(ext) for ext in known):
        return name
    if "voice" in (file_path or "").lower() or "voice" in lower:
        return f"{name}.oga"
    return name


async def fetch_whatsapp_media(media_id: str, principal_id: Any) -> dict[str, Any]:
    token = (settings.whatsapp_access_token or "").strip()
    if not token:
        return {"ok": False, "error": "whatsapp_token_missing"}
    version = settings.whatsapp_api_version or "v21.0"
    async with httpx.AsyncClient(timeout=60.0) as client:
        meta = await client.get(
            f"https://graph.facebook.com/{version}/{media_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        if meta.status_code >= 400:
            return {"ok": False, "error": f"media_meta_failed:{meta.status_code}"}
        info = meta.json()
        url = info.get("url")
        mime = info.get("mime_type")
        if not url:
            return {"ok": False, "error": "no_media_url"}
        data_resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
        if data_resp.status_code >= 400:
            return {"ok": False, "error": f"download_failed:{data_resp.status_code}"}
        data = data_resp.content

    ext = _ext_from_mime(mime or "") or ""
    name = f"wa-{media_id[:12]}{ext}"
    dest_dir = principal_workspace(principal_id) / "media"
    path = safe_write_bytes(dest_dir, name, data)
    return {
        "ok": True,
        "path": str(path),
        "size": len(data),
        "mime": mime or _guess_mime(name),
        "sha256": content_hash(data),
        "channel": "whatsapp",
        "media_id": media_id,
    }


async def fetch_telegram_media(
    file_id: str,
    principal_id: Any,
    *,
    filename_hint: str | None = None,
) -> dict[str, Any]:
    token = (settings.telegram_bot_token or "").strip()
    if not token:
        return {"ok": False, "error": "telegram_token_missing"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        meta = await client.get(
            f"https://api.telegram.org/bot{token}/getFile",
            params={"file_id": file_id},
        )
        if meta.status_code >= 400:
            return {"ok": False, "error": f"getFile_failed:{meta.status_code}"}
        payload = meta.json()
        if not payload.get("ok"):
            return {"ok": False, "error": "getFile_not_ok", "detail": payload}
        file_path = (payload.get("result") or {}).get("file_path")
        if not file_path:
            return {"ok": False, "error": "no_file_path"}
        data_resp = await client.get(f"https://api.telegram.org/file/bot{token}/{file_path}")
        if data_resp.status_code >= 400:
            return {"ok": False, "error": f"download_failed:{data_resp.status_code}"}
        data = data_resp.content

    name = _ensure_suffix(filename_hint or Path(file_path).name or f"tg-{uuid4().hex[:10]}", file_path)
    dest_dir = principal_workspace(principal_id) / "media"
    path = safe_write_bytes(dest_dir, name, data)
    return {
        "ok": True,
        "path": str(path),
        "size": len(data),
        "mime": _guess_mime(name, file_path),
        "sha256": content_hash(data),
        "channel": "telegram",
        "file_id": file_id,
        "telegram_path": file_path,
    }


def _ext_from_mime(mime: str) -> str:
    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "audio/ogg": ".ogg",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "video/mp4": ".mp4",
        "application/pdf": ".pdf",
        "text/plain": ".txt",
    }
    return mapping.get(mime, "")
