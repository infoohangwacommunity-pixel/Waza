"""
Fetch inbound media into the AI workspace.

WhatsApp: Graph API media URL → download → workspace/media/
Telegram: getFile → download → workspace/media/

The tutor receives a local path, not a permanent CDN dependency.
Optional later: multimodal model only when terminal extraction is insufficient.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from wax.config import get_settings
from wax.observability.logging import get_logger
from wax.terminal.workspace import principal_workspace, safe_write_bytes, content_hash

logger = get_logger(__name__)
settings = get_settings()


async def fetch_whatsapp_media(media_id: str, principal_id: Any, filename_hint: str | None = None) -> dict[str, Any]:
    if not settings.whatsapp_access_token or not media_id:
        return {"ok": False, "error": "whatsapp_media_unavailable"}

    version = getattr(settings, "whatsapp_api_version", "v21.0")
    headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
    meta_url = f"https://graph.facebook.com/{version}/{media_id}"

    async with httpx.AsyncClient(timeout=60.0) as client:
        meta = await client.get(meta_url, headers=headers)
        if meta.status_code >= 400:
            return {"ok": False, "error": f"meta_failed:{meta.status_code}", "body": meta.text[:200]}
        info = meta.json()
        download_url = info.get("url")
        mime = info.get("mime_type") or "application/octet-stream"
        if not download_url:
            return {"ok": False, "error": "no_download_url"}

        data_resp = await client.get(download_url, headers=headers)
        if data_resp.status_code >= 400:
            return {"ok": False, "error": f"download_failed:{data_resp.status_code}"}
        data = data_resp.content

    ext = _ext_from_mime(mime)
    name = filename_hint or f"wa-{media_id[:16]}{ext}"
    dest_dir = principal_workspace(principal_id) / "media"
    path = safe_write_bytes(dest_dir, name, data)
    return {
        "ok": True,
        "path": str(path),
        "size": len(data),
        "mime": mime,
        "sha256": content_hash(data),
        "channel": "whatsapp",
        "media_id": media_id,
    }


async def fetch_telegram_media(file_id: str, principal_id: Any, filename_hint: str | None = None) -> dict[str, Any]:
    token = settings.telegram_bot_token
    if not token or not file_id:
        return {"ok": False, "error": "telegram_media_unavailable"}

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

    name = filename_hint or Path(file_path).name or f"tg-{uuid4().hex[:10]}"
    # Telegram voice notes often arrive as voice/*.oga — preserve a recognizable suffix
    # so local transcription can treat them as audio without hardcoding product modes.
    lower = name.lower()
    if not any(lower.endswith(ext) for ext in (".oga", ".ogg", ".opus", ".mp3", ".m4a", ".wav", ".webm")):
        if "voice" in (file_path or "").lower() or lower.endswith(".oga"):
            name = f"{name}.oga"
        elif "voice" in lower:
            name = f"{name}.oga"
    dest_dir = principal_workspace(principal_id) / "media"
    path = safe_write_bytes(dest_dir, name, data)
    lower = str(path).lower()
    mime = None
    if lower.endswith((".oga", ".ogg", ".opus")):
        mime = "audio/ogg"
    elif lower.endswith((".mp4", ".mov", ".mkv", ".m4v")):
        mime = "video/mp4"
    elif lower.endswith((".jpg", ".jpeg")):
        mime = "image/jpeg"
    elif lower.endswith(".png"):
        mime = "image/png"
    elif lower.endswith(".pdf"):
        mime = "application/pdf"
    return {
        "ok": True,
        "path": str(path),
        "size": len(data),
        "mime": mime,
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
