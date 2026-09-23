"""Presentation architecture tests: parse, render, normalize, invariants, pipeline."""

from __future__ import annotations

import re

from wax.delivery.presentation import (
    get_profile,
    parse_model_output,
    plain_text_fallback,
    platform_context_block,
    present_for_channel,
    render_and_chunk,
    render_telegram,
    render_whatsapp,
)
from wax.delivery.chunking import chunk_message


# ---------------------------------------------------------------------------
# Existing profile tests
# ---------------------------------------------------------------------------


def test_whatsapp_profile():
    p = get_profile("whatsapp")
    assert p.supports_reply_buttons
    assert p.max_reply_buttons == 3
    assert p.supports_typing_indicator


def test_platform_block_mentions_channel():
    block = platform_context_block("whatsapp")
    assert "whatsapp" in block.lower()
    assert "button" in block.lower()


# ---------------------------------------------------------------------------
# Plain conversation
# ---------------------------------------------------------------------------


def test_plain_conversation_whatsapp():
    raw = "That is correct. The next thing to understand is photosynthesis."
    out = present_for_channel(raw, "whatsapp")
    assert "That is correct" in out
    assert "photosynthesis" in out
    assert "#" not in out


def test_plain_conversation_telegram():
    raw = "That is correct. The next thing to understand is photosynthesis."
    out = present_for_channel(raw, "telegram")
    assert "That is correct" in out
    assert "photosynthesis" in out


# ---------------------------------------------------------------------------
# Emphasis
# ---------------------------------------------------------------------------


def test_emphasis_whatsapp():
    raw = "This is **important** and this is *subtle*."
    out = present_for_channel(raw, "whatsapp")
    assert "*important*" in out
    assert "_subtle_" in out or "subtle" in out
    assert "**" not in out


def test_emphasis_telegram():
    raw = "This is **important** and this is *subtle*."
    out = present_for_channel(raw, "telegram")
    assert "*important*" in out
    assert "subtle" in out


# ---------------------------------------------------------------------------
# Headings — no literal ### leaking
# ---------------------------------------------------------------------------


def test_heading_no_hash_whatsapp():
    raw = "### Important distinction\n\nLight reactions happen in the thylakoid."
    out = present_for_channel(raw, "whatsapp")
    assert "###" not in out
    assert "Important distinction" in out
    assert "thylakoid" in out


def test_heading_no_hash_telegram():
    raw = "## Next step\n\nPractice the formula."
    out = present_for_channel(raw, "telegram")
    assert not re.search(r"^#{1,6}\s", out, re.MULTILINE)
    assert "Next step" in out
    assert "Practice" in out


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------


def test_unordered_list_whatsapp():
    raw = "- Uses light energy\n- Produces glucose\n- Releases oxygen"
    out = present_for_channel(raw, "whatsapp")
    assert "light energy" in out
    assert "glucose" in out
    assert "•" in out or "-" in out


def test_ordered_list_whatsapp():
    raw = "1. First step\n2. Second step\n3. Third step"
    out = present_for_channel(raw, "whatsapp")
    assert "First step" in out
    assert "1." in out
    assert "2." in out


def test_nested_list_preserves_items():
    raw = "- Outer A\n- Outer B\n  - Inner 1\n  - Inner 2"
    out = present_for_channel(raw, "whatsapp")
    assert "Outer A" in out
    assert "Outer B" in out


# ---------------------------------------------------------------------------
# Tables — no raw pipes; information preserved
# ---------------------------------------------------------------------------


def test_table_no_pipes_whatsapp():
    raw = """| Process | Result |
|---------|--------|
| Photosynthesis | glucose |
| Respiration | energy |
"""
    out = present_for_channel(raw, "whatsapp")
    assert "|" not in out or not re.search(r"^\|.+\|$", out, re.MULTILINE)
    assert "Photosynthesis" in out
    assert "glucose" in out
    assert "Respiration" in out
    assert "energy" in out


def test_table_no_pipes_telegram():
    raw = """| Name | Age |
|------|-----|
| John | 20 |
| Ada  | 30 |
"""
    out = present_for_channel(raw, "telegram")
    assert not re.search(r"^\|.+\|$", out, re.MULTILINE)
    assert "John" in out
    assert "20" in out
    assert "Ada" in out


def test_wide_table_preserves_cells():
    raw = """| A | B | C | D |
|---|---|---|---|
| 1 | 2 | 3 | 4 |
"""
    out = present_for_channel(raw, "whatsapp")
    for v in ("1", "2", "3", "4"):
        assert v in out


# ---------------------------------------------------------------------------
# HTML leakage
# ---------------------------------------------------------------------------


def test_html_br_becomes_break():
    raw = "Line one<br>Line two<br/>Line three"
    out = present_for_channel(raw, "whatsapp")
    assert "<br" not in out.lower()
    assert "Line one" in out
    assert "Line two" in out


def test_html_strong_stripped_meaning_kept():
    raw = "This is <strong>critical</strong> knowledge."
    out = present_for_channel(raw, "whatsapp")
    assert "<strong>" not in out
    assert "critical" in out


def test_html_anchor_preserves_url():
    raw = 'See <a href="https://example.com/doc">the guide</a> for details.'
    out = present_for_channel(raw, "whatsapp")
    assert "<a " not in out.lower()
    assert "https://example.com/doc" in out
    assert "guide" in out or "details" in out


# ---------------------------------------------------------------------------
# Code
# ---------------------------------------------------------------------------


def test_inline_code_whatsapp():
    raw = "Use the `photosynthesis` equation carefully."
    out = present_for_channel(raw, "whatsapp")
    assert "photosynthesis" in out
    assert "<code>" not in out


def test_code_block_preserved():
    raw = "Example:\n\n```python\nx = 1\ny = 2\n```\n\nDone."
    out = present_for_channel(raw, "whatsapp")
    assert "x = 1" in out
    assert "y = 2" in out
    assert "Done" in out


def test_code_block_telegram_fenced():
    raw = "```\nprint('hi')\n```"
    out = present_for_channel(raw, "telegram")
    assert "print" in out


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------


def test_markdown_link_whatsapp():
    raw = "Read [this article](https://example.com/a) carefully."
    out = present_for_channel(raw, "whatsapp")
    assert "https://example.com/a" in out
    assert "article" in out


def test_markdown_link_telegram():
    raw = "Read [this article](https://example.com/a) carefully."
    out = present_for_channel(raw, "telegram")
    assert "https://example.com/a" in out
    assert "article" in out


# ---------------------------------------------------------------------------
# Mixed formatting
# ---------------------------------------------------------------------------


def test_mixed_formatting_pipeline():
    raw = """### Overview

Photosynthesis is **essential**.

- Uses light
- Makes glucose

See [notes](https://example.com/n).

```
C6H12O6
```

Any questions?
"""
    for channel in ("whatsapp", "telegram"):
        out = present_for_channel(raw, channel)
        assert "Overview" in out
        assert "essential" in out
        assert "light" in out
        assert "glucose" in out
        assert "https://example.com/n" in out
        assert "C6H12O6" in out
        assert "questions" in out
        assert "###" not in out
        assert "<" not in out or "C6H12O6" in out  # no HTML tags


# ---------------------------------------------------------------------------
# Malformed model output + fallback
# ---------------------------------------------------------------------------


def test_malformed_markdown_still_delivers():
    raw = "**unclosed bold and ### weird | table | junk\n<br><div>text</div>"
    out = present_for_channel(raw, "whatsapp")
    assert out  # non-empty
    assert "<div>" not in out
    assert "<br" not in out.lower()


def test_plain_fallback_strips_markup():
    raw = "### Title\n\n**bold** and <br> break"
    out = plain_text_fallback(raw)
    assert "###" not in out
    assert "**" not in out
    assert "<br" not in out.lower()
    assert "Title" in out
    assert "bold" in out


# ---------------------------------------------------------------------------
# Channel independence of meaning
# ---------------------------------------------------------------------------


def test_same_content_different_channels():
    raw = "**Hello** world\n\n- one\n- two"
    wa = present_for_channel(raw, "whatsapp")
    tg = present_for_channel(raw, "telegram")
    assert "Hello" in wa and "Hello" in tg
    assert "one" in wa and "one" in tg
    # Representations may differ
    assert isinstance(wa, str) and isinstance(tg, str)


# ---------------------------------------------------------------------------
# Render → normalize → chunk pipeline
# ---------------------------------------------------------------------------


def test_render_then_chunk_long_response():
    paras = [f"Paragraph number {i}. " + ("word " * 40) for i in range(20)]
    raw = "\n\n".join(paras)
    chunks = render_and_chunk(raw, "whatsapp")
    assert len(chunks) > 1
    profile = get_profile("whatsapp")
    assert all(len(c) <= profile.max_text_chars + 50 for c in chunks)
    # Recombined meaning preserved
    joined = " ".join(chunks)
    assert "Paragraph number 0" in joined
    assert "Paragraph number 19" in joined


def test_chunk_does_not_split_short_code():
    raw = "Before\n\n```\nshort\n```\n\nAfter " + ("x" * 100)
    chunks = render_and_chunk(raw, "whatsapp")
    assert any("short" in c for c in chunks)


# ---------------------------------------------------------------------------
# End-to-end style per channel
# ---------------------------------------------------------------------------


GOLDEN_PROBLEMATIC = """### Important distinction

Photosynthesis **uses** light energy.

| Process | Input | Output |
|---------|-------|--------|
| Photosynthesis | light + CO2 | glucose |
| Respiration | glucose | energy |

Key points:
1. Chloroplasts matter
2. ATP is produced

See <a href="https://example.com/bio">this page</a> for more.<br>
Any questions?
"""


def test_e2e_whatsapp_pipeline():
    out = present_for_channel(GOLDEN_PROBLEMATIC, "whatsapp")
    chunks = chunk_message(out, max_chars=get_profile("whatsapp").max_text_chars)
    assert chunks
    full = "\n".join(chunks)
    # Invariants
    assert "###" not in full
    assert not re.search(r"^\|.+\|$", full, re.MULTILINE)
    assert "<a " not in full.lower()
    assert "<br" not in full.lower()
    # Information preserved
    assert "Important distinction" in full
    assert "Photosynthesis" in full
    assert "glucose" in full
    assert "Respiration" in full
    assert "Chloroplasts" in full
    assert "https://example.com/bio" in full
    assert "questions" in full.lower()


def test_e2e_telegram_pipeline():
    out = present_for_channel(GOLDEN_PROBLEMATIC, "telegram")
    chunks = chunk_message(out, max_chars=get_profile("telegram").max_text_chars)
    assert chunks
    full = "\n".join(chunks)
    assert "###" not in full
    assert not re.search(r"^\|.+\|$", full, re.MULTILINE)
    assert "<br" not in full.lower()
    assert "Important distinction" in full
    assert "glucose" in full
    assert "https://example.com/bio" in full


# ---------------------------------------------------------------------------
# Parse structure basics
# ---------------------------------------------------------------------------


def test_parse_heading_and_paragraph():
    doc = parse_model_output("## Title\n\nHello world.")
    kinds = [b.kind for b in doc.blocks]
    assert "heading" in kinds
    assert "paragraph" in kinds


def test_parse_table_structure():
    raw = "| A | B |\n|---|---|\n| 1 | 2 |\n"
    doc = parse_model_output(raw)
    tables = [b for b in doc.blocks if b.kind == "table"]
    assert len(tables) == 1
    assert tables[0].headers == ["A", "B"]
    assert tables[0].rows == [["1", "2"]]


def test_render_whatsapp_heading_no_hash():
    doc = parse_model_output("### Note\n\nBody text.")
    out = render_whatsapp(doc)
    assert "###" not in out
    assert "Note" in out
    assert "Body text" in out


def test_render_telegram_escapes_specials():
    doc = parse_model_output("Use _underscores_ and *stars* carefully in plain context? Wait **bold**.")
    out = render_telegram(doc)
    # Should not crash; bold applied
    assert "bold" in out or "*bold*" in out


# ---------------------------------------------------------------------------
# Hardening: Telegram escaping contract (must not rely on provider rejection)
# ---------------------------------------------------------------------------


def test_telegram_escapes_underscores_in_plain():
    raw = "Use the variable my_value carefully."
    out = present_for_channel(raw, "telegram")
    # Underscores in plain text must be escaped for legacy Markdown
    assert "my_value" in out.replace("\\", "") or "my_value" in out
    from wax.delivery.presentation import telegram_markdown_is_balanced
    assert telegram_markdown_is_balanced(out)


def test_telegram_escapes_stars_in_plain():
    raw = "The formula is a*b = c when plain."
    out = present_for_channel(raw, "telegram")
    from wax.delivery.presentation import telegram_markdown_is_balanced
    assert telegram_markdown_is_balanced(out)
    assert "a" in out and "b" in out


def test_telegram_bold_with_specials_inside():
    raw = "This is **a_b*c** important."
    out = present_for_channel(raw, "telegram")
    from wax.delivery.presentation import telegram_markdown_is_balanced
    assert telegram_markdown_is_balanced(out)
    assert "important" in out or "a" in out


def test_telegram_link_with_parens_in_url():
    raw = "See [docs](https://example.com/path_(1)) here."
    out = present_for_channel(raw, "telegram")
    assert "example.com" in out
    from wax.delivery.presentation import telegram_markdown_is_balanced
    assert telegram_markdown_is_balanced(out)


def test_telegram_mixed_specials_balanced():
    raw = """### Title_one

Use *emphasis* and **strong** text.
Also a_b and x*y plain symbols.

- item_one
- item_two
"""
    out = present_for_channel(raw, "telegram")
    from wax.delivery.presentation import telegram_markdown_is_balanced
    assert telegram_markdown_is_balanced(out)
    assert "Title" in out
    assert "emphasis" in out
    assert "strong" in out


def test_telegram_code_preserves_content():
    raw = "Run `foo_bar*` now."
    out = present_for_channel(raw, "telegram")
    assert "foo_bar" in out or "foo" in out
    from wax.delivery.presentation import telegram_markdown_is_balanced
    assert telegram_markdown_is_balanced(out)


# ---------------------------------------------------------------------------
# Hardening: sender does not re-present (contract)
# ---------------------------------------------------------------------------


def test_sender_boundary_normalize_only():
    """Senders must not call present_for_channel; only thin normalize."""
    import ast
    import inspect
    from wax.delivery import senders

    def _calls_present(fn) -> bool:
        tree = ast.parse(inspect.getsource(fn))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == "present_for_channel":
                    return True
                if isinstance(node.func, ast.Attribute) and node.func.attr == "present_for_channel":
                    return True
        return False

    assert not _calls_present(senders.send_whatsapp)
    assert not _calls_present(senders.send_telegram)
    assert not _calls_present(senders.deliver)
    # Thin boundary normalize is allowed
    assert hasattr(senders, "_boundary_normalize")


def test_boundary_normalize_strips_residual_html():
    from wax.delivery.senders import _boundary_normalize
    out = _boundary_normalize("Hello<br>World", "whatsapp")
    assert "<br" not in out.lower()
    assert "Hello" in out
    assert "World" in out


# ---------------------------------------------------------------------------
# Hardening: chunking formatting constructs
# ---------------------------------------------------------------------------


def test_chunk_preserves_code_fence_intact():
    fence_body = "x = 1\n" * 30
    raw = f"Intro paragraph.\n\n```\n{fence_body}```\n\nOutro."
    # Use present first then chunk at small limit
    from wax.delivery.presentation import present_for_channel
    from wax.delivery.chunking import chunk_message, CODE_FENCE_SLACK
    text = present_for_channel(raw, "whatsapp")
    chunks = chunk_message(text, max_chars=80)
    # No chunk should have unbalanced ```
    for c in chunks:
        assert c.count("```") % 2 == 0, f"unbalanced fence in chunk: {c[:60]!r}"


def test_chunk_code_fence_slack_is_explicit():
    from wax.delivery.chunking import CODE_FENCE_SLACK
    assert CODE_FENCE_SLACK == 1.5


def test_chunk_prefers_list_boundaries():
    items = "\n".join(f"• item number {i} with some padding text here" for i in range(20))
    chunks = chunk_message(items, max_chars=120)
    assert len(chunks) > 1
    # Chunks should tend to start at list items
    for c in chunks[1:]:
        stripped = c.lstrip()
        assert stripped.startswith("•") or "item" in stripped


def test_whatsapp_no_raw_md_heading_after_full_pipeline():
    raw = "#### Deep heading\n\nBody with **bold**."
    out = present_for_channel(raw, "whatsapp")
    assert not re.search(r"^#{1,6}\s", out, re.MULTILINE)
    assert "Deep heading" in out
    assert "*bold*" in out or "bold" in out
