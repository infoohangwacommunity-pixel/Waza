"""
Channel-aware presentation.

Tells the intelligence the platform limits so it writes well.
Advanced splitting of long output (not rate-limiting).
AI may request interactive controls; we only use them when appropriate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from wax.config import get_settings
from wax.delivery.chunking import chunk_message

settings = get_settings()


@dataclass
class ChannelProfile:
    name: str
    max_text_chars: int
    supports_markdown: bool
    supports_reply_buttons: bool
    max_reply_buttons: int
    supports_lists: bool
    max_list_rows: int
    supports_typing_indicator: bool
    notes: str


PROFILES: dict[str, ChannelProfile] = {
    "whatsapp": ChannelProfile(
        name="whatsapp",
        max_text_chars=settings.whatsapp_max_message_chars or 3500,
        supports_markdown=False,  # limited formatting
        supports_reply_buttons=True,
        max_reply_buttons=3,
        supports_lists=True,
        max_list_rows=10,
        supports_typing_indicator=True,
        notes=(
            "WhatsApp: keep messages readable. Prefer short paragraphs. "
            "Bold with *text*, italic with _text_. Avoid giant walls. "
            "If a response is long, structure it so it can be split on paragraphs. "
            "You may offer up to 3 quick-reply buttons OR a list (up to 10 options) "
            "when a clear choice would help the learner — never as a rigid menu for everything. "
            "Do not use buttons when free-form conversation is better."
        ),
    ),
    "telegram": ChannelProfile(
        name="telegram",
        max_text_chars=settings.telegram_max_message_chars or 4000,
        supports_markdown=True,
        supports_reply_buttons=True,
        max_reply_buttons=8,
        supports_lists=False,
        max_list_rows=0,
        supports_typing_indicator=True,
        notes=(
            "Telegram: Markdown is available. Keep messages readable. "
            "Optional reply keyboard buttons when a clear choice helps."
        ),
    ),
    "web": ChannelProfile(
        name="web",
        max_text_chars=12000,
        supports_markdown=True,
        supports_reply_buttons=True,
        max_reply_buttons=12,
        supports_lists=True,
        max_list_rows=20,
        supports_typing_indicator=False,
        notes="Web UI: richer formatting and longer responses are fine.",
    ),
}


def get_profile(channel: str) -> ChannelProfile:
    return PROFILES.get(channel, PROFILES["whatsapp"])


def platform_context_block(channel: str) -> str:
    """Inject into the tutor system prompt so the model adapts output."""
    p = get_profile(channel)
    return (
        f"\n--- Delivery channel: {p.name} ---\n"
        f"Max practical message length: ~{p.max_text_chars} characters per bubble.\n"
        f"{p.notes}\n"
        f"Write so the response is perfect for this channel. "
        f"If content is long, use clear paragraph breaks so it can be split cleanly.\n"
        f"--- End channel constraints ---\n"
    )


@dataclass
class InteractiveChoice:
    id: str
    title: str
    description: str | None = None


@dataclass
class PresentableResponse:
    """Normalized tutor output ready for channel delivery."""

    text: str
    interactive_type: str | None = None  # reply_buttons | list | None
    buttons: list[InteractiveChoice] = field(default_factory=list)
    list_button_label: str | None = None
    list_sections: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def text_chunks(self, channel: str) -> list[str]:
        p = get_profile(channel)
        return chunk_message(self.text, max_chars=p.max_text_chars)
