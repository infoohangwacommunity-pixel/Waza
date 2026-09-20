"""
Channel delivery adapters.

WhatsApp and Telegram outbound. Keep channel logic out of the tutor core.
Supports typing (elsewhere), smart chunking, and AI-requested interactives.
"""

from __future__ import annotations

from typing import Any

import httpx

from wax.config import get_settings
from wax.delivery.chunking import chunk_message
from wax.delivery.presentation import (
    InteractiveChoice,
    PresentableResponse,
    get_profile,
)
from wax.observability.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


def build_presentable(text: str, interactive: dict | None = None) -> PresentableResponse:
    if not interactive or not interactive.get("ok"):
        return PresentableResponse(text=text)
    style = interactive.get("style") or "buttons"
    choices = [
        InteractiveChoice(
            # Prefer opaque callback_data so WhatsApp button id matches Interaction lookup
            id=c.get("callback_data") or c.get("id") or f"opt_{i}",
            title=c.get("title") or f"Option {i}",
            description=c.get("description"),
        )
        for i, c in enumerate(interactive.get("choices") or [])
    ]
    if style == "list" and len(choices) > 3:
        sections = [
            {
                "title": "Options",
                "rows": [
                    {
                        "id": c.id,
                        "title": c.title[:24],
                        **({"description": c.description[:72]} if c.description else {}),
                    }
                    for c in choices[:10]
                ],
            }
        ]
        return PresentableResponse(
            text=text,
            interactive_type="list",
            list_button_label=interactive.get("list_button_label") or "Options",
            list_sections=sections,
        )
    return PresentableResponse(
        text=text,
        interactive_type="reply_buttons",
        buttons=choices[:3],
    )


async def send_whatsapp(to: str, text: str, interactive: dict | None = None) -> dict[str, Any]:
    if not settings.whatsapp_enabled or not settings.whatsapp_access_token:
        return {"status": "skipped", "reason": "whatsapp_not_configured"}
    from wax.messaging.whatsapp.client import deliver_presentable

    presentable = build_presentable(text, interactive)
    return await deliver_presentable(to, presentable)


async def send_telegram(chat_id: str, text: str, interactive: dict | None = None) -> dict[str, Any]:
    if not settings.telegram_enabled or not settings.telegram_bot_token:
        return {"status": "skipped", "reason": "telegram_not_configured"}

    profile = get_profile("telegram")
    chunks = chunk_message(text, max_chars=profile.max_text_chars)
    results = []
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=30.0) as client:
        for i, chunk in enumerate(chunks):
            body: dict[str, Any] = {"chat_id": chat_id, "text": chunk}
            # Attach reply keyboard only on last chunk if choices present
            if (
                interactive
                and interactive.get("ok")
                and i == len(chunks) - 1
                and interactive.get("choices")
            ):
                choices = interactive["choices"][:8]
                # Prefer inline keyboard when durable callback_data is present
                if any(c.get("callback_data") for c in choices):
                    ik = []
                    row = []
                    for c in choices:
                        title = str(c.get("title") or c.get("id") or "OK")[:64]
                        cb = str(c.get("callback_data") or c.get("id") or title)[:64]
                        row.append({"text": title, "callback_data": cb})
                        if len(row) == 2:
                            ik.append(row)
                            row = []
                    if row:
                        ik.append(row)
                    body["reply_markup"] = {"inline_keyboard": ik}
                else:
                    rows = []
                    row = []
                    for c in choices:
                        title = c.get("title") or c.get("id") or "OK"
                        row.append({"text": str(title)[:64]})
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
            resp = await client.post(url, json=body)
            results.append({"status_code": resp.status_code, "body": resp.text[:300]})
            if resp.status_code >= 400:
                logger.error("telegram_send_failed", status=resp.status_code)
                return {"status": "failed", "results": results}
    return {"status": "ok", "chunks": len(chunks), "results": results}


async def deliver(
    channel: str,
    target: str,
    text: str,
    interactive: dict | None = None,
) -> dict[str, Any]:
    if channel == "whatsapp":
        return await send_whatsapp(target, text, interactive)
    if channel == "telegram":
        return await send_telegram(target, text, interactive)
    return {"status": "failed", "reason": f"unknown_channel:{channel}"}
