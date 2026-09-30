"""
Inbound message normalization across WhatsApp and Telegram.

Produces a channel-agnostic structure the rest of the system understands.
Pure transport normalization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class NormalizedInbound:
    channel: str
    external_event_id: str
    external_user_id: str
    text: str
    content_type: str = "text"  # text | image | audio | video | document | interactive | other
    interactive_id: str | None = None
    media_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    display_name: str | None = None


def normalize_whatsapp_message(msg: dict[str, Any], contacts: dict | None = None) -> NormalizedInbound | None:
    external_id = msg.get("id")
    wa_id = msg.get("from")
    if not external_id or not wa_id:
        return None

    msg_type = msg.get("type") or "text"
    text = ""
    content_type = "text"
    interactive_id = None
    media_id = None

    if msg_type == "text":
        text = (msg.get("text") or {}).get("body") or ""
    elif msg_type == "interactive":
        content_type = "interactive"
        inter = msg.get("interactive") or {}
        if inter.get("type") == "button_reply":
            br = inter.get("button_reply") or {}
            text = br.get("title") or br.get("id") or ""
            interactive_id = br.get("id")
        elif inter.get("type") == "list_reply":
            lr = inter.get("list_reply") or {}
            text = lr.get("title") or lr.get("id") or ""
            interactive_id = lr.get("id")
        else:
            text = "[interactive]"
    elif msg_type == "button":
        content_type = "interactive"
        btn = msg.get("button") or {}
        text = btn.get("text") or btn.get("payload") or ""
        interactive_id = btn.get("payload")
    elif msg_type in ("image", "audio", "document", "video", "sticker"):
        content_type = msg_type if msg_type != "sticker" else "image"
        media = msg.get(msg_type) or {}
        media_id = media.get("id")
        text = media.get("caption") or f"[{msg_type} received]"
    elif msg_type == "location":
        content_type = "other"
        loc = msg.get("location") or {}
        # Factual fields only — AI decides meaning
        text = (
            f"[location latitude={loc.get('latitude')} "
            f"longitude={loc.get('longitude')} "
            f"name={loc.get('name') or ''} address={loc.get('address') or ''}]"
        )
    elif msg_type == "contacts":
        content_type = "other"
        contacts_list = msg.get("contacts") or []
        names = []
        for c in contacts_list[:5]:
            n = ((c.get("name") or {}).get("formatted_name") or "")[:80]
            if n:
                names.append(n)
        text = f"[contacts shared: {', '.join(names) or 'count=' + str(len(contacts_list))}]"
    elif msg_type == "reaction":
        content_type = "other"
        react = msg.get("reaction") or {}
        text = f"[reaction emoji={react.get('emoji') or ''} to={react.get('message_id') or ''}]"
    else:
        content_type = "other"
        text = f"[{msg_type} message]"

    display = None
    if contacts and wa_id in contacts:
        display = (contacts[wa_id].get("profile") or {}).get("name")

    return NormalizedInbound(
        channel="whatsapp",
        external_event_id=external_id,
        external_user_id=wa_id,
        text=text,
        content_type=content_type,
        interactive_id=interactive_id,
        media_id=media_id,
        raw=msg,
        display_name=display,
    )


def normalize_telegram_update(payload: dict[str, Any]) -> NormalizedInbound | None:
    message = payload.get("message") or payload.get("edited_message")
    if not message:
        return None
    chat = message.get("chat") or {}
    from_user = message.get("from") or {}
    chat_id = str(chat.get("id") or "")
    msg_id = str(message.get("message_id") or "")
    if not chat_id or not msg_id:
        return None

    text = message.get("text") or message.get("caption") or ""
    content_type = "text"
    media_id = None

    if message.get("photo"):
        content_type = "image"
        photos = message["photo"]
        media_id = str(photos[-1].get("file_id")) if photos else None
        if not text:
            text = "[image received]"
    elif message.get("document"):
        content_type = "document"
        media_id = str((message["document"] or {}).get("file_id") or "")
        if not text:
            text = "[document received]"
    elif message.get("voice") or message.get("audio"):
        content_type = "audio"
        media_id = str(((message.get("voice") or message.get("audio")) or {}).get("file_id") or "")
        if not text:
            text = "[audio received]"
    elif message.get("video") or message.get("video_note"):
        content_type = "video"
        media_id = str(
            ((message.get("video") or message.get("video_note")) or {}).get("file_id") or ""
        )
        if not text:
            text = "[video received]"
    elif not text:
        text = "[message]"
        content_type = "other"

    name = from_user.get("first_name") or from_user.get("username")
    return NormalizedInbound(
        channel="telegram",
        external_event_id=f"{chat_id}:{msg_id}",
        external_user_id=chat_id,
        text=text,
        content_type=content_type,
        media_id=media_id,
        raw=message,
        display_name=name,
    )
