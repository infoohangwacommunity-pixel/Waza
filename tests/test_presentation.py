from wax.delivery.presentation import get_profile, platform_context_block


def test_whatsapp_profile():
    p = get_profile("whatsapp")
    assert p.supports_reply_buttons
    assert p.max_reply_buttons == 3
    assert p.supports_typing_indicator


def test_platform_block_mentions_channel():
    block = platform_context_block("whatsapp")
    assert "whatsapp" in block.lower()
    assert "button" in block.lower()
