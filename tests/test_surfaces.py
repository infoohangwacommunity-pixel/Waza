"""Web Surface architecture tests — runtime, tokens, policy, wrap."""

from __future__ import annotations

from datetime import timedelta

from wax.surfaces.policy import (
    DEFAULT_SURFACE_POLICY,
    CapabilityScope,
    SurfaceStatus,
    can_transition,
)
from wax.surfaces.tokens import generate_token, hash_token, tokens_match
from wax.surfaces.runtime import wrap_ai_html, render_unavailable, inject_runtime_bridge


def test_surface_token_security():
    t = generate_token()
    assert len(t) >= 40
    h = hash_token(t)
    assert tokens_match(t, h)
    assert not tokens_match(t[:-1] + "x", h)


def test_lifecycle_transitions():
    assert can_transition(SurfaceStatus.CREATING, SurfaceStatus.ACTIVE)
    assert can_transition(SurfaceStatus.ACTIVE, SurfaceStatus.EXPIRED)
    assert can_transition(SurfaceStatus.ACTIVE, SurfaceStatus.REVOKED)
    assert can_transition(SurfaceStatus.IDLE, SurfaceStatus.ACTIVE)
    assert not can_transition(SurfaceStatus.CLEANED, SurfaceStatus.ACTIVE)
    assert not can_transition("bogus", "active")


def test_lifetime_policy_bounds():
    p = DEFAULT_SURFACE_POLICY
    assert p.resolve_lifetime(None) == p.default_lifetime
    assert p.resolve_lifetime(0.01) == p.min_lifetime
    assert p.resolve_lifetime(99999) == p.max_lifetime


def test_wrap_fragment_gets_bridge():
    html = wrap_ai_html("<h1>Hello</h1><p>World</p>", title="Test")
    assert "WAX.surface" in html
    assert "/s/" in html or "{{SURFACE_TOKEN}}" in html or "SURFACE_API_BASE" in html
    assert "Hello" in html
    assert "<!DOCTYPE html>" in html


def test_wrap_full_document_injects_bridge():
    full = "<!DOCTYPE html><html><body><div id='app'>X</div></body></html>"
    out = wrap_ai_html(full, title="App")
    assert "WAX.surface" in out
    assert "id='app'" in out or 'id="app"' in out or "id='app'" in out


def test_future_unknown_experience_no_nodetype():
    """AI invents a novel visual experience — no NodeType required."""
    weird = """
    <div class="galaxy">
      <canvas id="c"></canvas>
      <script>
        // pure local animation — no AI call
        const c = document.getElementById('c');
        if (c) { c.width = 300; c.height = 150; }
      </script>
      <style>.galaxy { background: radial-gradient(#001, #000); min-height: 40vh; }</style>
    </div>
    """
    out = wrap_ai_html(weird, title="Galaxy")
    assert "canvas" in out
    assert "WAX.surface" in out
    # No educational taxonomy forced
    assert "NodeType" not in out
    assert "StudyPlan" not in out


def test_multi_purpose_workspace_html():
    """One surface can hold notes + interactive + chat affordance."""
    html = """
    <section id="notes"><textarea id="pad">Biology notes...</textarea></section>
    <section id="sim"><button id="run">Run simulation</button></section>
    <section id="ask"><input id="q"/><button id="go">Ask WAX</button></section>
    <script>
      document.getElementById('go')?.addEventListener('click', async () => {
        const msg = document.getElementById('q')?.value;
        if (window.WAX?.surface) await window.WAX.surface.askAI(msg);
      });
      document.getElementById('pad')?.addEventListener('change', async (e) => {
        if (window.WAX?.surface) await window.WAX.surface.patchState({ notes: e.target.value });
      });
    </script>
    """
    out = wrap_ai_html(html, title="Workspace")
    assert "notes" in out and "sim" in out and "askAI" in out


def test_unavailable_pages():
    for r in ("expired", "revoked", "not_found", "forbidden"):
        h = render_unavailable(reason=r)
        assert "unavailable" in h.lower() or "withdrawn" in h.lower() or "couldn't" in h.lower() or "don't" in h.lower()
        assert "WAX" in h


def test_capability_scopes_exist():
    assert CapabilityScope.VIEW.value == "view"
    assert CapabilityScope.AI_REQUEST.value == "ai_request"
    assert CapabilityScope.STATE_WRITE.value == "state_write"


def test_bridge_has_no_secrets():
    b = inject_runtime_bridge("TESTTOKEN")
    assert "DATABASE" not in b
    assert "SECRET" not in b
    assert "API_KEY" not in b
    assert "/s/" in b and "/api" in b


def test_bridge_exposes_status_and_revision_not_work_id():
    from wax.surfaces.runtime import inject_runtime_bridge
    b = inject_runtime_bridge("TOK")
    assert "aiStatus" in b
    assert "watchRevision" in b
    assert "work_id" not in b
    assert "DATABASE" not in b
    assert "serviceWorker" in b  # unregister


def test_wrap_does_not_require_title_document():
    from wax.surfaces.runtime import wrap_ai_html
    out = wrap_ai_html("<canvas></canvas>", title="")
    assert "canvas" in out
    assert "WAX.surface" in out


def test_scopes_are_least_privilege_names():
    from wax.surfaces.policy import CapabilityScope
    names = {s.value for s in CapabilityScope}
    assert "view" in names
    assert "database" not in names
    assert "admin" not in names


def test_bridge_uses_api_base_placeholder():
    b = inject_runtime_bridge("TOK", api_base_placeholder="https://s.example.com")
    assert "https://s.example.com" in b
    assert "credentials: \"omit\"" in b or "credentials: \"omit\"" in b.replace(" ", "")
    assert "service_workers_disabled" in b


def test_bridge_relative_when_no_api_base():
    b = inject_runtime_bridge("TOK", api_base_placeholder="")
    assert "SURFACE_API_BASE" not in b or 'API_BASE = ""' in b
    assert "/s/" in b


def test_surface_origin_prefers_dedicated():
    try:
        from wax.surfaces.service import SurfaceService
    except ImportError:
        import pytest
        pytest.skip("sqlalchemy not installed in test env")
    from unittest.mock import MagicMock
    svc = SurfaceService(MagicMock())
    class S:
        public_base_url = "https://app.example.com"
        surface_public_origin = "https://s.example.com"
    svc.settings = S()
    assert svc.surface_origin() == "https://s.example.com"
    assert svc.public_url("abc") == "https://s.example.com/s/abc"
    assert svc.api_base_for_bridge() == "https://s.example.com"



def test_surface_origin_falls_back():
    try:
        from wax.surfaces.service import SurfaceService
    except ImportError:
        import pytest
        pytest.skip("sqlalchemy not installed in test env")
    from unittest.mock import MagicMock
    svc = SurfaceService(MagicMock())
    class S:
        public_base_url = "https://app.example.com"
        surface_public_origin = ""
    svc.settings = S()
    assert svc.surface_origin() == "https://app.example.com"
    assert svc.api_base_for_bridge() == ""



def test_wrap_injects_api_base_placeholder():
    out = wrap_ai_html("<p>x</p>", api_base_placeholder="{{SURFACE_API_BASE}}")
    assert "{{SURFACE_API_BASE}}" in out
    assert "{{SURFACE_TOKEN}}" in out


def test_no_secrets_in_wrapped_html():
    out = wrap_ai_html("<p>hi</p>", title="T")
    for banned in ("DATABASE_URL", "OPENAI", "ANTHROPIC", "API_KEY", "postgres://", "SECRET_KEY"):
        assert banned not in out


def test_service_worker_disabled_in_bridge():
    b = inject_runtime_bridge("T")
    assert "service_workers_disabled" in b
    assert "unregister" in b
