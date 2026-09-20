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


async def send_document(
    chat_id: str,
    *,
    filename: str,
    data: bytes,
    caption: str | None = None,
    content_type: str = "application/octet-stream",
) -> dict[str, Any]:
    """Upload a document to Telegram (multipart). Returns provider message id when possible."""
    if not settings.telegram_enabled or not settings.telegram_bot_token:
        return {"status": "skipped", "reason": "telegram_not_configured"}
    if len(data) > 50 * 1024 * 1024:
        return {"status": "failed", "reason": "file_too_large"}
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            files = {
                "document": (filename, data, content_type),
            }
            form = {"chat_id": str(chat_id)}
            if caption:
                form["caption"] = caption[:1024]
            resp = await client.post(_api("sendDocument"), data=form, files=files)
            body = {}
            try:
                body = resp.json()
            except Exception:
                body = {"raw": resp.text[:300]}
            if resp.status_code >= 400 or not body.get("ok"):
                logger.error(
                    "telegram_document_failed",
                    status=resp.status_code,
                    body=str(body)[:300],
                )
                return {"status": "failed", "status_code": resp.status_code, "body": body}
            result = body.get("result") or {}
            msg_id = result.get("message_id")
            doc = result.get("document") or {}
            file_id = doc.get("file_id")
            logger.info(
                "artifact_delivery_succeeded",
                channel="telegram",
                chat_id=str(chat_id),
                message_id=msg_id,
                file_id=file_id,
            )
            return {
                "status": "ok",
                "external_message_id": str(msg_id) if msg_id is not None else None,
                "file_id": file_id,
                "provider": "telegram",
            }
    except Exception as e:
        logger.exception("telegram_document_error")
        return {"status": "failed", "error": str(e)}


async def send_inline_choices(
    chat_id: str,
    text: str,
    choices: list[dict[str, Any]],
    *,
    interaction_prefix: str = "ix",
) -> dict[str, Any]:
    """Send message with inline keyboard. callback_data is opaque interaction token."""
    if not settings.telegram_enabled or not settings.telegram_bot_token:
        return {"status": "skipped", "reason": "telegram_not_configured"}
    # Telegram callback_data max 64 bytes
    keyboard = []
    row = []
    for i, c in enumerate(choices[:8]):
        cid = str(c.get("callback_data") or c.get("id") or f"{interaction_prefix}:{i}")[:64]
        title = str(c.get("title") or c.get("id") or "OK")[:64]
        row.append({"text": title, "callback_data": cid})
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    body = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {"inline_keyboard": keyboard},
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(_api("sendMessage"), json=body)
        data = {}
        try:
            data = resp.json()
        except Exception:
            data = {}
        if resp.status_code >= 400 or not data.get("ok"):
            return {"status": "failed", "status_code": resp.status_code, "body": data}
        msg_id = (data.get("result") or {}).get("message_id")
        return {
            "status": "ok",
            "external_message_id": str(msg_id) if msg_id is not None else None,
            "provider": "telegram",
        }


async def clear_inline_keyboard(chat_id: str, message_id: int | str) -> dict[str, Any]:
    """Remove inline keyboard after a choice is consumed."""
    if not settings.telegram_enabled or not settings.telegram_bot_token:
        return {"status": "skipped"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            _api("editMessageReplyMarkup"),
            json={"chat_id": chat_id, "message_id": int(message_id), "reply_markup": {}},
        )
        return {"status": "ok" if resp.status_code < 400 else "failed"}
