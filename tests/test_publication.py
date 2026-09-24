"""Publication semantic model + renderer smoke tests."""

from __future__ import annotations

from wax.publication.schema import PublicationDocument, normalize_document, Block, BlockType
from wax.publication.renderer import render_document, render_expired_page, RENDERER_VERSION
from wax.publication.tokens import generate_public_token, hash_token, tokens_match


def test_token_roundtrip():
    t = generate_public_token()
    h = hash_token(t)
    assert tokens_match(t, h)
    assert not tokens_match(t + "x", h)
    assert len(t) >= 40


def test_normalize_simple():
    doc = normalize_document({
        "title": "Study notes",
        "blocks": [
            {"type": "heading", "text": "Section 1", "level": 2},
            {"type": "paragraph", "text": "Hello <script>alert(1)</script>"},
            {"type": "list", "items": ["a", "b"]},
            {"type": "table", "rows": [
                {"cells": [{"text": "H1", "header": True}, {"text": "H2", "header": True}]},
                {"cells": [{"text": "1"}, {"text": "2"}]},
            ]},
            {"type": "callout", "tone": "tip", "text": "Remember this"},
        ],
    })
    assert isinstance(doc, PublicationDocument)
    assert doc.title == "Study notes"
    html = render_document(doc, expires_at_iso="2026-09-25T12:00:00+00:00")
    assert "Study notes" in html
    assert "<script>" not in html  # escaped
    assert "alert(1)" in html  # text present but escaped
    assert "WAX PREP" in html
    assert "wx-table" in html
    assert RENDERER_VERSION


def test_expired_page():
    html = render_expired_page(reason="expired")
    assert "no longer available" in html
    assert "WAX" in html


def test_unforeseen_blocks():
    """Content-agnostic: unknown-ish composition still renders."""
    doc = normalize_document({
        "title": "Unexpected lab procedure",
        "blocks": [
            {"type": "timeline", "items": ["Prep", "Run", "Clean"]},
            {"type": "code", "language": "bash", "text": "echo hello"},
            {"type": "formula", "text": "E = mc^2"},
            {"type": "expandable", "title": "Details", "text": "More info"},
        ],
    })
    html = render_document(doc)
    assert "lab procedure" in html
    assert "wx-timeline" in html
    assert "echo hello" in html
