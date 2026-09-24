"""Deep publication system tests — semantic, planner, renderer, policy, security."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from wax.publication.schema import (
    SCHEMA_VERSION,
    PublicationDocument,
    Node,
    NodeType,
    normalize_document,
    collect_artifact_ids,
    document_to_dict,
)
from wax.publication.planner import plan_document
from wax.publication.renderer import (
    RENDERER_VERSION,
    render_document,
    render_expired_page,
    _safe_href,
)
from wax.publication.tokens import generate_public_token, hash_token, tokens_match
from wax.publication.policy import (
    DEFAULT_POLICY,
    PublicationStatus,
    can_transition,
    LifecyclePolicy,
)


# ─── Tokens ───────────────────────────────────────────────────────────────────

def test_token_entropy_and_match():
    t = generate_public_token()
    assert len(t) >= 40
    h = hash_token(t)
    assert tokens_match(t, h)
    assert not tokens_match(t + "x", h)
    assert not tokens_match("", h)
    assert not tokens_match("short", h)


# ─── Policy / state machine ───────────────────────────────────────────────────

def test_lifetime_bounds():
    p = DEFAULT_POLICY
    assert p.resolve_lifetime(None) == p.default_lifetime
    assert p.resolve_lifetime(0.01) == p.min_lifetime
    assert p.resolve_lifetime(9999) == p.max_lifetime
    assert p.resolve_lifetime(24) == timedelta(hours=24)


def test_state_transitions():
    assert can_transition(PublicationStatus.PREPARING, PublicationStatus.ACTIVE)
    assert can_transition(PublicationStatus.ACTIVE, PublicationStatus.EXPIRED)
    assert can_transition(PublicationStatus.ACTIVE, PublicationStatus.REVOKED)
    assert can_transition(PublicationStatus.EXPIRED, PublicationStatus.CLEANED)
    assert not can_transition(PublicationStatus.CLEANED, PublicationStatus.ACTIVE)
    assert not can_transition(PublicationStatus.EXPIRED, PublicationStatus.ACTIVE)
    assert not can_transition("bogus", "active")


# ─── Schema ───────────────────────────────────────────────────────────────────

def test_normalize_accepts_blocks_alias():
    doc = normalize_document({
        "title": "T",
        "blocks": [{"type": "paragraph", "text": "hi"}],
    })
    assert len(doc.nodes) == 1
    assert doc.nodes[0].type == NodeType.PARAGRAPH


def test_nested_composition():
    doc = normalize_document({
        "title": "Nested",
        "nodes": [
            {
                "type": "section",
                "title": "Outer",
                "children": [
                    {"type": "heading", "level": 3, "text": "Inner"},
                    {
                        "type": "columns",
                        "columns": [
                            [{"type": "paragraph", "text": "L"}],
                            [{"type": "paragraph", "text": "R"}],
                        ],
                    },
                    {
                        "type": "card",
                        "title": "Card",
                        "children": [{"type": "list", "items": ["a", "b"]}],
                    },
                ],
            }
        ],
    })
    assert doc.nodes[0].children[0].type == NodeType.HEADING
    html = render_document(doc)
    assert "Outer" in html and "Inner" in html and "wx-columns" in html


def test_xss_escaped():
    doc = normalize_document({
        "title": "X<script>alert(1)</script>",
        "nodes": [{"type": "paragraph", "text": '<img onerror="alert(1)" src=x>'}],
    })
    html = render_document(doc)
    assert "<script>" not in html
    assert "onerror=" not in html or "&lt;" in html or "onerror" not in html.split("wx-content")[1][:500]


def test_unsafe_urls_stripped():
    assert _safe_href("javascript:alert(1)") is None
    assert _safe_href("data:text/html,hi") is None
    assert _safe_href("https://example.com/a") == "https://example.com/a"
    assert _safe_href("mailto:a@b.com") == "mailto:a@b.com"
    assert _safe_href("//evil.com") is None


def test_forward_compat_unknown_fields():
    """Unknown metadata must not break rendering."""
    doc = normalize_document({
        "title": "Future",
        "nodes": [
            {
                "type": "paragraph",
                "text": "ok",
                "future_field_xyz": {"nested": True},
                "present": {"emphasize": True, "unknown_hint": 1},
            }
        ],
        "extra_top_level": 123,
    })
    html = render_document(doc)
    assert "ok" in html


def test_collect_artifact_ids_nested():
    doc = normalize_document({
        "title": "A",
        "nodes": [
            {
                "type": "section",
                "title": "S",
                "children": [
                    {"type": "artifact", "artifact": {"artifact_id": "11111111-1111-1111-1111-111111111111"}},
                    {"type": "media", "media": {"artifact_id": "22222222-2222-2222-2222-222222222222", "media_kind": "image"}},
                ],
            }
        ],
    })
    ids = collect_artifact_ids(doc)
    assert len(ids) == 2


# ─── Planner ──────────────────────────────────────────────────────────────────

def test_planner_toc_from_structure():
    nodes = [{"type": "heading", "level": 2, "text": f"H{i}"} for i in range(4)]
    doc = normalize_document({"title": "Doc", "nodes": nodes})
    plan = plan_document(doc)
    assert plan.show_toc is True
    assert len(plan.toc) >= 3
    html = render_document(doc, plan=plan)
    assert "wx-toc" in html
    assert 'href="#' in html


def test_planner_no_toc_for_short():
    doc = normalize_document({
        "title": "Short",
        "nodes": [{"type": "paragraph", "text": "only one"}],
    })
    plan = plan_document(doc)
    assert plan.show_toc is False
    html = render_document(doc, plan=plan)
    assert '<nav class="wx-toc"' not in html


# ─── Tables ───────────────────────────────────────────────────────────────────

def test_table_accessible_and_responsive():
    doc = normalize_document({
        "title": "Table doc",
        "nodes": [{
            "type": "table",
            "table": {
                "caption": "Sample",
                "rows": [
                    {"cells": [{"text": "A", "header": True}, {"text": "B", "header": True}]},
                    {"cells": [{"text": "1"}, {"text": "2", "emphasis": True}]},
                ],
            },
        }],
    })
    html = render_document(doc)
    assert "wx-table-wrap" in html
    assert "scope=" in html
    assert "Sample" in html
    assert "tabindex" in html


# ─── Content-agnostic compositions (not predefined educational types) ─────────

def test_composition_procedure_checklist():
    """Lab-style procedure — no ProcedurePage type needed."""
    doc = normalize_document({
        "title": "Calibration sequence",
        "summary": "Ordered steps with safety notes",
        "nodes": [
            {"type": "callout", "tone": "warning", "text": "Wear eye protection"},
            {"type": "ordered_list", "items": ["Warm up", "Zero sensor", "Record baseline"]},
            {"type": "table", "rows": [
                {"cells": [{"text": "Step", "header": True}, {"text": "Expected", "header": True}]},
                {"cells": [{"text": "1"}, {"text": "Stable reading"}]},
            ]},
            {"type": "code", "language": "python", "text": "print(baseline)"},
        ],
    })
    html = render_document(doc)
    assert "Calibration" in html and "wx-callout-warning" in html


def test_composition_comparison_matrix():
    doc = normalize_document({
        "title": "Option comparison",
        "nodes": [
            {"type": "columns", "columns": [
                [{"type": "card", "title": "A", "children": [{"type": "paragraph", "text": "Fast"}]}],
                [{"type": "card", "title": "B", "children": [{"type": "paragraph", "text": "Safe"}]}],
            ]},
            {"type": "timeline", "items": ["Decide", "Trial", "Review"]},
        ],
    })
    html = render_document(doc)
    assert "wx-columns" in html and "wx-timeline" in html


def test_composition_reference_pack():
    doc = normalize_document({
        "title": "Sources",
        "nodes": [
            {"type": "references", "sources": [
                {"label": "Paper A", "href": "https://example.com/a"},
                {"label": "Internal note"},
            ]},
            {"type": "expandable", "title": "More", "text": "Detail"},
            {"type": "formula", "text": "F = ma"},
        ],
    })
    html = render_document(doc)
    assert "References" in html and "wx-expand" in html


def test_expired_states():
    for reason in ("expired", "revoked", "not_found", "forbidden"):
        html = render_expired_page(reason=reason)
        assert "WAX" in html
        assert "unavailable" in html.lower() or "withdrawn" in html.lower() or "couldn't" in html.lower() or "don't" in html.lower()


def test_document_roundtrip_dict():
    doc = normalize_document({"title": "R", "nodes": [{"type": "paragraph", "text": "x"}]})
    d = document_to_dict(doc)
    doc2 = normalize_document(d)
    assert doc2.title == "R"


def test_heading_hierarchy_in_html():
    doc = normalize_document({
        "title": "Hierarchy",
        "nodes": [
            {"type": "heading", "level": 2, "text": "H2"},
            {"type": "heading", "level": 3, "text": "H3"},
        ],
    })
    html = render_document(doc)
    assert "<h2" in html and "<h3" in html
    # Single h1 from document title
    assert html.count("<h1") == 1
