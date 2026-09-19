"""
Channel delivery adapters.

WhatsApp and Telegram outbound. Keep channel logic out of the tutor core.
"""

from __future__ import annotations

from typing import Any

import httpx

from wax.config import get_settings
from wax.delivery.chunking import chunk_message
from wax.observability.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


async def send_whatsapp(to: str, text: str) -> dict[str, Any]:
    if not settings.whatsapp_enabled or not settings.whatsapp_access_token:
        return {"status": "skipped", "reason": "whatsapp_not_configured"}

    chunks = chunk_message(text, max_chars=settings.whatsapp_max_message_chars)
    results = []
    url = (
        f"https://graph.facebook.com/{settings.whatsapp_api_version}/"
        f"{settings.whatsapp_phone_number_id}/messages"
    )
    headers = {
        "Authorization": f"Bearer {settings.whatsapp_access_token}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        for chunk in chunks:
            body = {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": chunk},
            }
            resp = await client.post(url, headers=headers, json=body)
            results.append({"status_code": resp.status_code, "body": resp.text[:300]})
            if resp.status_code >= 400:
                logger.error("whatsapp_send_failed", status=resp.status_code, body=resp.text[:200])
                return {"status": "failed", "results": results}
    return {"status": "ok", "chunks": len(chunks), "results": results}


async def send_telegram(chat_id: str, text: str) -> dict[str, Any]:
    if not settings.telegram_enabled or not settings.telegram_bot_token:
        return {"status": "skipped", "reason": "telegram_not_configured"}

    chunks = chunk_message(text, max_chars=settings.telegram_max_message_chars)
    results = []
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=30.0) as client:
        for chunk in chunks:
            body = {"chat_id": chat_id, "text": chunk}
            resp = await client.post(url, json=body)
            results.append({"status_code": resp.status_code, "body": resp.text[:300]})
            if resp.status_code >= 400:
                logger.error("telegram_send_failed", status=resp.status_code)
                return {"status": "failed", "results": results}
    return {"status": "ok", "chunks": len(chunks), "results": results}


async def deliver(channel: str, target: str, text: str) -> dict[str, Any]:
    if channel == "whatsapp":
        return await send_whatsapp(target, text)
    if channel == "telegram":
        return await send_telegram(target, text)
    return {"status": "failed", "reason": f"unknown_channel:{channel}"}
