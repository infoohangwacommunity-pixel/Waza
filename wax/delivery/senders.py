"""
Channel delivery adapters.

Contract:
  Presentation pipeline produces channel-valid text and stores it on Delivery.content.
  Senders receive already-rendered text and deliver it — they do NOT re-run presentation.

  Presentation → channel-valid text → Sender → provider

Defensive normalize at the boundary is a thin safety net only (residual HTML / tables),
not a second full render pass.

Interactive payloads remain separate from ordinary text delivery.
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
    normalize_for_channel,
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


def _boundary_normalize(text: str, channel: str) -> str:
    """
    Thin delivery-boundary safety net only.
    Does not re-parse or re-render. Catches residual HTML / MD tables / headings
    if Delivery.content was somehow written without going through presentation.
    """
    try:
        return normalize_for_channel(text or "", channel)
    except Exception:
        return (text or "").strip()


async def send_whatsapp(to: str, text: str, interactive: dict | None = None) -> dict[str, Any]:
    """
    Deliver already channel-rendered WhatsApp text.
    `text` must be WhatsApp-safe (from present_for_channel / Delivery.content).
    """
    if not settings.whatsapp_enabled or not settings.whatsapp_access_token:
        return {"status": "skipped", "reason": "whatsapp_not_configured"}
    from wax.messaging.whatsapp.client import deliver_presentable

    safe = _boundary_normalize(text, "whatsapp")
    presentable = build_presentable(safe, interactive)
    return await deliver_presentable(to, presentable)


async def send_telegram(chat_id: str, text: str, interactive: dict | None = None) -> dict[str, Any]:
    """
    Deliver already channel-rendered Telegram text (Markdown parse_mode).
    `text` must be Telegram-safe from the Telegram renderer.
    Last-resort plain fallback only if the provider rejects parse_mode — not the
    normal correctness path; renderer tests must guarantee valid output.
    """
    if not settings.telegram_enabled or not settings.telegram_bot_token:
        return {"status": "skipped", "reason": "telegram_not_configured"}

    safe = _boundary_normalize(text, "telegram")
    profile = get_profile("telegram")
    chunks = chunk_message(safe, max_chars=profile.max_text_chars)
    results = []
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=30.0) as client:
        for i, chunk in enumerate(chunks):
            body: dict[str, Any] = {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "Markdown",
            }
            # Attach reply keyboard only on last chunk if choices present
            if (
                interactive
                and interactive.get("ok")
                and i == len(chunks) - 1
                and interactive.get("choices")
            ):
                choices = interactive["choices"][:8]
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
                # Last-resort reliability only — not the correctness mechanism
                logger.warning(
                    "telegram_parse_mode_rejected",
                    status=resp.status_code,
                    body=(resp.text or "")[:200],
                )
                body_plain = {k: v for k, v in body.items() if k != "parse_mode"}
                resp2 = await client.post(url, json=body_plain)
                results.append(
                    {
                        "status_code": resp2.status_code,
                        "body": resp2.text[:300],
                        "fallback": "plain",
                    }
                )
                if resp2.status_code >= 400:
                    logger.error("telegram_send_failed", status=resp2.status_code)
                    return {"status": "failed", "results": results}
    return {"status": "ok", "chunks": len(chunks), "results": results}


async def send_typing(channel: str, target: str, *, inbound_message_id: str | None = None) -> dict[str, Any]:
    """
    Best-effort typing / read indicator. Never blocks durable work on failure.
    WhatsApp requires the inbound message id for typing_indicator+read.
    """
    profile = get_profile(channel)
    if not getattr(profile, "supports_typing_indicator", False):
        return {"status": "skipped", "reason": "channel_no_typing"}
    try:
        if channel == "telegram":
            from wax.messaging.telegram.client import send_typing as tg_typing

            return await tg_typing(target)
        if channel == "whatsapp":
            from wax.messaging.whatsapp.client import send_typing_and_read

            if not inbound_message_id:
                return {"status": "skipped", "reason": "whatsapp_needs_message_id"}
            return await send_typing_and_read(inbound_message_id)
    except Exception as e:
        logger.warning("typing_indicator_failed", channel=channel, error=str(e)[:200])
        return {"status": "failed", "error": str(e)[:200]}
    return {"status": "skipped", "reason": f"unknown_channel:{channel}"}


async def deliver(
    channel: str,
    target: str,
    text: str,
    interactive: dict | None = None,
    *,
    inbound_message_id: str | None = None,
    show_typing: bool = True,
) -> dict[str, Any]:
    """
    Deliver already channel-rendered text.

    Callers (tutor, scheduled path, engine) must run present_for_channel before
    writing Delivery.content. Senders do not re-present.
    """
    try:
        from wax.security.safe_errors import sanitize_for_student
        text = sanitize_for_student(text)
    except Exception:
        pass
    if show_typing:
        await send_typing(channel, target, inbound_message_id=inbound_message_id)
    if channel == "whatsapp":
        return await send_whatsapp(target, text, interactive)
    if channel == "telegram":
        return await send_telegram(target, text, interactive)
    return {"status": "failed", "reason": f"unknown_channel:{channel}"}
