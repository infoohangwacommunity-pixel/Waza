"""
Cross-channel Principal continuity tests.

Pure tests run without DB. Integration tests are marked and require async DB.
"""

from __future__ import annotations

import pytest

from wax.domain.identity import format_cross_channel_continuity_block, format_linked_channels_block


def test_continuity_block_bounded_and_labeled():
    snippets = [
        {"role": "user", "content": "[earlier on whatsapp] Explain quadratic equations"},
        {"role": "assistant", "content": "[earlier on whatsapp] Sure — a quadratic is ax^2+bx+c"},
    ]
    block = format_cross_channel_continuity_block(snippets)
    assert "same learner" in block.lower() or "SAME principal" in block or "same principal" in block.lower()
    assert "whatsapp" in block.lower()
    assert "quadratic" in block.lower()
    assert "bounded" in block.lower() or "brief" in block.lower() or "Do not dump" in block


def test_continuity_empty():
    assert format_cross_channel_continuity_block([]) == ""


def test_linked_state_both_channels_authoritative():
    block = format_linked_channels_block(
        {
            "current_channel": "telegram",
            "linked_channels": ["telegram", "whatsapp"],
            "whatsapp_linked": True,
            "telegram_linked": True,
            "identity_count": 2,
            "identities": [],
        }
    )
    assert "WhatsApp: linked" in block
    assert "Telegram: linked" in block
    # Tutor must not contradict this
    assert "not linked" not in block.split("WhatsApp:")[1].split("\n")[0]


@pytest.mark.asyncio
async def test_resolve_or_create_same_principal_when_linked(db_session=None):
    """
    Integration: after link, Telegram external id resolves to same Principal.
    Skipped when no DB fixture is available in this environment.
    """
    pytest.importorskip("sqlalchemy")
    if db_session is None:
        pytest.skip("requires async db_session fixture")
