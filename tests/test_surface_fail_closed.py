"""Surface origin: same-origin PUBLIC_BASE_URL is supported; production needs a public URL."""

from __future__ import annotations

from unittest.mock import MagicMock

from wax.surfaces.tokens import deterministic_token, hash_token, tokens_match
from wax.surfaces.runtime import inject_runtime_bridge, wrap_ai_html


def test_deterministic_token_stable():
    a = deterministic_token("surface", "pid", "idem-1")
    b = deterministic_token("surface", "pid", "idem-1")
    c = deterministic_token("surface", "pid", "idem-2")
    assert a == b
    assert a != c
    assert tokens_match(a, hash_token(a))
    assert len(a) >= 32


def test_bridge_does_not_expose_token_property():
    b = inject_runtime_bridge("SECRETTOKENVALUE")
    assert "token: TOKEN" not in b
    assert "SECRETTOKENVALUE" in b
    assert "service_workers_disabled" in b


def test_wrap_has_navigate_safe_defaults():
    out = wrap_ai_html("<p>x</p>")
    assert "WAX.surface" in out
    assert "credentials" in out


def test_production_allows_same_origin_public_base_url():
    """Railway-style deploy: only PUBLIC_BASE_URL — Surfaces must not fail closed."""
    try:
        from wax.surfaces.service import SurfaceService
    except ImportError:
        import pytest
        pytest.skip("sqlalchemy not installed")

    svc = SurfaceService(MagicMock())

    class S:
        app_env = "production"
        public_base_url = "https://web-production-b83b4.up.railway.app"
        surface_public_origin = ""

    svc.settings = S()
    assert svc.require_origin_isolation() is None
    assert svc.surface_origin() == "https://web-production-b83b4.up.railway.app"
    assert svc.public_url("tok123") == "https://web-production-b83b4.up.railway.app/s/tok123"
    assert svc.isolated_origin_configured() is False  # optional dedicated host not set


def test_production_refuses_when_no_public_origin_at_all():
    try:
        from wax.surfaces.service import SurfaceService
    except ImportError:
        import pytest
        pytest.skip("sqlalchemy not installed")

    svc = SurfaceService(MagicMock())

    class S:
        app_env = "production"
        public_base_url = ""
        surface_public_origin = ""

    svc.settings = S()
    assert svc.require_origin_isolation() == "public_origin_required"


def test_optional_dedicated_origin_still_works():
    try:
        from wax.surfaces.service import SurfaceService
    except ImportError:
        import pytest
        pytest.skip("sqlalchemy not installed")

    svc = SurfaceService(MagicMock())

    class S:
        app_env = "production"
        public_base_url = "https://app.example.com"
        surface_public_origin = "https://s.example.com"

    svc.settings = S()
    assert svc.require_origin_isolation() is None
    assert svc.isolated_origin_configured() is True
    assert svc.surface_origin() == "https://s.example.com"
    assert svc.api_base_for_bridge() == "https://s.example.com"


def test_require_origin_isolation_dev_allows_fallback():
    try:
        from wax.surfaces.service import SurfaceService
    except ImportError:
        import pytest
        pytest.skip("sqlalchemy not installed")

    svc = SurfaceService(MagicMock())

    class S:
        app_env = "development"
        public_base_url = "http://localhost:8000"
        surface_public_origin = ""

    svc.settings = S()
    assert svc.require_origin_isolation() is None
    assert svc.surface_origin() == "http://localhost:8000"


def test_architecture_idempotency_doc_present():
    from pathlib import Path
    doc = Path(__file__).resolve().parents[1] / "docs" / "WEB_SURFACE_ARCHITECTURE.md"
    text = doc.read_text()
    assert "PUBLIC_BASE_URL" in text
    assert "same origin" in text.lower() or "Same origin" in text
