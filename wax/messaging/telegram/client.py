"""
Telegram Bot API client — typing, chunked text, optional reply keyboards.
"""

from __future__ import annotations

from typing import Any

import httpx

from wax.config import get_settings
from wax.delivery.chunking import chunk_message
from wax.delivery.presentation import get_profile
from wax.observability.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


def _api(method: str) -> str:
    return f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"


async def send_typing(chat_id: str) -> dict[str, Any]:
    if not settings.telegram_enabled or not settings.telegram_bot_token:
        return {"status": "skipped"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                _api("sendChatAction"),
                json={"chat_id": chat_id, "action": "typing"},
            )
        return {"status": "ok" if resp.status_code < 400 else "failed"}
    except Exception as e:
        return {"status": "failed", "error": str(e)}


async def send_text(
    chat_id: str,
    text: str,
    *,
    choices: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not settings.telegram_enabled or not settings.telegram_bot_token:
        return {"status": "skipped", "reason": "telegram_not_configured"}

    profile = get_profile("telegram")
    chunks = chunk_message(text, max_chars=profile.max_text_chars)
    results = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        for i, chunk in enumerate(chunks):
            body: dict[str, Any] = {"chat_id": chat_id, "text": chunk}
            if choices and i == len(chunks) - 1:
                rows: list[list[dict[str, str]]] = []
                row: list[dict[str, str]] = []
                for c in choices[:8]:
                    title = str(c.get("title") or c.get("id") or "OK")[:64]
                    row.append({"text": title})
                    if len(row) == 2:
                        rows.append(row)
                        row = []
                if row:
                    rows.append(row)
                body["reply_markup"] = {
                    "keyboard": rows,
                    "one_time_keyboard": True,
                    "resize_keyboard": True,
                }
            resp = await client.post(_api("sendMessage"), json=body)
            results.append({"status_code": resp.status_code})
            if resp.status_code >= 400:
                logger.error("telegram_send_failed", status=resp.status_code, body=resp.text[:200])
                return {"status": "failed", "results": results}
    return {"status": "ok", "chunks": len(chunks), "results": results}
