"""Production fail-closed origin, deterministic tokens, CSP navigation."""

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
    # Token still in closure for fetch URLs, but not as .token property
    assert "token: TOKEN" not in b
    assert "SECRETTOKENVALUE" in b  # still used in API path construction
    assert "service_workers_disabled" in b


def test_wrap_has_navigate_safe_defaults():
    out = wrap_ai_html("<p>x</p>")
    assert "WAX.surface" in out
    assert "credentials" in out


def test_require_origin_isolation_production():
    try:
        from wax.surfaces.service import SurfaceService
    except ImportError:
        import pytest
        pytest.skip("sqlalchemy not installed")

    svc = SurfaceService(MagicMock())

    class S:
        app_env = "production"
        public_base_url = "https://app.example.com"
        surface_public_origin = ""

    svc.settings = S()
    assert svc.require_origin_isolation() == "origin_isolation_required"
    assert svc.isolated_origin_configured() is False

    svc.settings.surface_public_origin = "https://app.example.com"
    assert svc.require_origin_isolation() == "origin_isolation_required"

    svc.settings.surface_public_origin = "https://s.example.com"
    assert svc.require_origin_isolation() is None
    assert svc.isolated_origin_configured() is True


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


def test_architecture_idempotency_unique_in_migration():
    src = open("alembic/versions/016_surface_hardening.py").read()
    assert "uq_surface_ai_req_idempotency" in src
    assert "state_revision" in src


def test_no_request_id_null_in_service():
    src = open("wax/surfaces/service.py").read()
    assert '"request_id": None' not in src
    assert "deterministic_token" in src
    assert "state_conflict" in src
    assert "origin_isolation_required" in src
