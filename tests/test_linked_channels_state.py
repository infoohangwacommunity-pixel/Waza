"""Authoritative linked-channel state for tutor context."""

from wax.domain.identity import format_linked_channels_block


def test_format_shows_whatsapp_and_telegram_linked():
    state = {
        "current_channel": "telegram",
        "linked_channels": ["telegram", "whatsapp"],
        "whatsapp_linked": True,
        "telegram_linked": True,
        "identity_count": 2,
        "identities": [],
    }
    block = format_linked_channels_block(state)
    assert "WhatsApp: linked" in block
    assert "Telegram: linked" in block
    assert "Current interface: telegram" in block
    assert "authoritative" in block.lower() or "AUTHORITATIVE" in block or "Use only this state" in block


def test_format_shows_unlinked_clearly():
    state = {
        "current_channel": "whatsapp",
        "linked_channels": ["whatsapp"],
        "whatsapp_linked": True,
        "telegram_linked": False,
        "identity_count": 1,
        "identities": [],
    }
    block = format_linked_channels_block(state)
    assert "WhatsApp: linked" in block
    assert "Telegram: not linked" in block
    # Tutor must not invent the opposite of this
    assert "not linked" in block


def test_empty_state_safe():
    assert format_linked_channels_block({}) == ""
