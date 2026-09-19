from wax.delivery.chunking import chunk_message


def test_code_fence_preference():
    text = "intro\n\n```\n" + ("x = 1\n" * 40) + "```\n\nafter"
    chunks = chunk_message(text, max_chars=80)
    assert len(chunks) >= 1
