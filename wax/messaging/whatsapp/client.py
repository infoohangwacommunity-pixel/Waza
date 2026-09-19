"""
WhatsApp Cloud API client.

Typing indicators, text (chunked), reply buttons, list messages.
AI decides when interactives are used — this layer only executes.
"""

from __future__ import annotations

from typing import Any

import httpx

from wax.config import get_settings
from wax.delivery.chunking import chunk_message
from wax.delivery.presentation import InteractiveChoice, PresentableResponse, get_profile
from wax.observability.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


def _base_url() -> str:
    return (
        f"https://graph.facebook.com/{settings.whatsapp_api_version}/"
        f"{settings.whatsapp_phone_number_id}"
    )


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.whatsapp_access_token}",
        "Content-Type": "application/json",
    }


async def send_typing_and_read(message_id: str) -> dict[str, Any]:
    """
    Mark inbound message as read and show typing indicator.
    Indicator lasts until we reply or ~25 seconds.
    """
    if not settings.whatsapp_enabled or not settings.whatsapp_access_token:
        return {"status": "skipped", "reason": "whatsapp_not_configured"}
    if not message_id:
        return {"status": "skipped", "reason": "no_message_id"}

    body = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
        "typing_indicator": {"type": "text"},
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{_base_url()}/messages", headers=_headers(), json=body)
        if resp.status_code >= 400:
            logger.warning("whatsapp_typing_failed", status=resp.status_code, body=resp.text[:200])
            return {"status": "failed", "code": resp.status_code}
        return {"status": "ok"}
    except Exception as e:
        logger.warning("whatsapp_typing_error", error=str(e))
        return {"status": "failed", "error": str(e)}


async def send_text(to: str, text: str) -> dict[str, Any]:
    profile = get_profile("whatsapp")
    chunks = chunk_message(text, max_chars=profile.max_text_chars)
    results = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        for chunk in chunks:
            body = {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": chunk},
            }
            resp = await client.post(f"{_base_url()}/messages", headers=_headers(), json=body)
            results.append({"status_code": resp.status_code, "body": resp.text[:300]})
            if resp.status_code >= 400:
                logger.error("whatsapp_text_failed", status=resp.status_code)
                return {"status": "failed", "results": results}
    return {"status": "ok", "chunks": len(chunks), "results": results}


async def send_reply_buttons(
    to: str,
    body_text: str,
    buttons: list[InteractiveChoice],
    *,
    footer: str | None = None,
) -> dict[str, Any]:
    """Up to 3 reply buttons. Titles max 20 chars."""
    btns = []
    for b in buttons[:3]:
        title = (b.title or "")[:20]
        if not title:
            continue
        btns.append(
            {
                "type": "reply",
                "reply": {"id": (b.id or title)[:256], "title": title},
            }
        )
    if not btns:
        return await send_text(to, body_text)

    interactive: dict[str, Any] = {
        "type": "button",
        "body": {"text": body_text[:1024]},
        "action": {"buttons": btns},
    }
    if footer:
        interactive["footer"] = {"text": footer[:60]}

    body = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": interactive,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{_base_url()}/messages", headers=_headers(), json=body)
    if resp.status_code >= 400:
        logger.error("whatsapp_buttons_failed", status=resp.status_code, body=resp.text[:300])
        # Fallback to plain text
        return await send_text(to, body_text)
    return {"status": "ok", "type": "reply_buttons"}


async def send_list(
    to: str,
    body_text: str,
    button_label: str,
    sections: list[dict[str, Any]],
    *,
    header: str | None = None,
    footer: str | None = None,
) -> dict[str, Any]:
    """List message: up to 10 rows across sections."""
    interactive: dict[str, Any] = {
        "type": "list",
        "body": {"text": body_text[:1024]},
        "action": {
            "button": (button_label or "Options")[:20],
            "sections": sections[:10],
        },
    }
    if header:
        interactive["header"] = {"type": "text", "text": header[:60]}
    if footer:
        interactive["footer"] = {"text": footer[:60]}

    body = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": interactive,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{_base_url()}/messages", headers=_headers(), json=body)
    if resp.status_code >= 400:
        logger.error("whatsapp_list_failed", status=resp.status_code, body=resp.text[:300])
        return await send_text(to, body_text)
    return {"status": "ok", "type": "list"}


async def deliver_presentable(to: str, presentable: PresentableResponse) -> dict[str, Any]:
    """
    Send a PresentableResponse:
    - optional interactive first message
    - remaining long text as smart chunks
    """
    if not settings.whatsapp_enabled or not settings.whatsapp_access_token:
        return {"status": "skipped", "reason": "whatsapp_not_configured"}

    outcomes: list[dict[str, Any]] = []

    if presentable.interactive_type == "reply_buttons" and presentable.buttons:
        # First bubble can be interactive with a concise body
        body = presentable.text
        # Prefer shorter body for button messages
        if len(body) > 900:
            chunks = chunk_message(body, max_chars=get_profile("whatsapp").max_text_chars)
            # send long content as text first, then buttons with summary
            for c in chunks:
                outcomes.append(await send_text(to, c))
            summary = "Choose an option:"
            outcomes.append(
                await send_reply_buttons(to, summary, presentable.buttons)
            )
        else:
            outcomes.append(
                await send_reply_buttons(to, body, presentable.buttons)
            )
        return {"status": "ok", "outcomes": outcomes}

    if presentable.interactive_type == "list" and presentable.list_sections:
        body = presentable.text
        if len(body) > 900:
            for c in chunk_message(body, max_chars=get_profile("whatsapp").max_text_chars):
                outcomes.append(await send_text(to, c))
            outcomes.append(
                await send_list(
                    to,
                    "Choose an option:",
                    presentable.list_button_label or "Options",
                    presentable.list_sections,
                )
            )
        else:
            outcomes.append(
                await send_list(
                    to,
                    body,
                    presentable.list_button_label or "Options",
                    presentable.list_sections,
                )
            )
        return {"status": "ok", "outcomes": outcomes}

    # Default: smart-chunked text only
    return await send_text(to, presentable.text)
