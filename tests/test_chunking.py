from wax.delivery.chunking import chunk_message


def test_short_message_unchanged():
    assert chunk_message("Hello") == ["Hello"]


def test_splits_on_paragraph():
    text = ("Para one.\n\n" * 200)
    chunks = chunk_message(text, max_chars=500)
    assert len(chunks) > 1
    assert all(len(c) <= 520 for c in chunks)


def test_empty():
    assert chunk_message("") == []
    assert chunk_message("   ") == []
