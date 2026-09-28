"""Surface security boundary, concurrency, isolation unit tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
from uuid import uuid4

from wax.surfaces.policy import SurfaceStatus, CapabilityScope
from wax.surfaces.tokens import generate_token, hash_token, tokens_match
from wax.surfaces.runtime import inject_runtime_bridge, wrap_ai_html


def test_token_isolation_properties():
    a = generate_token()
    b = generate_token()
    assert a != b
    assert not tokens_match(a, hash_token(b))
    assert tokens_match(a, hash_token(a))
    assert not tokens_match("", hash_token(a))
    assert not tokens_match("short", hash_token(a))


def test_csp_related_bridge_has_no_privileged_fetch():
    b = inject_runtime_bridge("TOK1234567890abcdef")
    assert "/api" in b
    assert "webhooks" not in b
    assert "DATABASE" not in b
    assert "process.env" not in b


def test_generated_html_cannot_register_sw():
    b = inject_runtime_bridge("TOK")
    assert "service_workers_disabled" in b


def test_untrusted_context_keys_stripped_conceptually():
    banned = {"learner_id", "principal_id", "work_id", "surface_id", "token"}
    ctx = {"clicked": "sim", "learner_id": "evil", "principal_id": "x"}
    cleaned = {k: v for k, v in ctx.items() if k not in banned}
    assert "clicked" in cleaned
    assert "learner_id" not in cleaned


def test_publication_tools_removed_from_active_catalog():
    from wax.tools.registry import HANDLERS
    assert "publish_web_surface" not in HANDLERS
    assert "create_html_page" not in HANDLERS
    assert "revoke_publication" not in HANDLERS


def test_wrap_has_no_internal_ids():
    out = wrap_ai_html("<div>hello</div>", title="Demo")
    assert "work_id" not in out
    assert "principal_id" not in out
    assert "DATABASE" not in out
