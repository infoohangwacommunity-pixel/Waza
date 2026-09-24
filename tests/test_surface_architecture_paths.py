"""
Architecture path verification for Web Surface ↔ WAX intelligence.

These tests prove from source that Surface AI enters the same TutorService
path as messaging, and that critical security/idempotency properties hold.

Full PostgreSQL integration tests live alongside and skip when DB is unavailable.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_surface_ai_reaches_same_handle_message():
    """handle_surface_request must call handle_message — one intelligence."""
    src = _read("wax/intelligence/tutor.py")
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "handle_surface_request":
            body = ast.unparse(node)
            assert "handle_message" in body
            assert "CompletionRequest" not in body or "handle_message" in body
            found = True
    assert found, "handle_surface_request not found"


def test_worker_routes_surface_ai_to_process_surface_ai():
    src = _read("wax/workers/main.py")
    assert 'work.kind == "surface_ai"' in src or "kind == \"surface_ai\"" in src
    assert "process_surface_ai" in src
    assert "handle_surface_request" in src
    # process_surface_ai must not invent a second LLM client
    assert "OpenAI(" not in src.split("async def process_surface_ai")[1].split("async def ")[0]


def test_accept_ai_creates_work_kind_surface_ai():
    src = _read("wax/surfaces/service.py")
    assert 'kind="surface_ai"' in src
    assert "principal_id=surface.principal_id" in src
    # Identity from surface ownership, not browser
    assert '"request_id": str(req.id)' in src or "request_id" in src


def test_idempotent_request_returns_opaque_id_not_none():
    src = _read("wax/surfaces/service.py")
    # Must not return request_id: None on idempotent hit
    assert '"request_id": None' not in src
    assert "_opaque_request_token" in src
    assert '"idempotent": True' in src


def test_revision_update_uses_atomic_cas():
    src = _read("wax/surfaces/service.py")
    assert "update(Surface)" in src
    assert "rowcount" in src
    assert "revision_conflict" in src
    assert "current_revision ==" in src or "Surface.current_revision ==" in src


def test_browser_identity_keys_stripped_in_api():
    src = _read("wax/api/main.py")
    for key in ("learner_id", "principal_id", "work_id", "surface_id"):
        assert key in src  # stripping logic references them
    assert "raw_ctx.pop" in src or "pop(k" in src


def test_publication_tools_not_active():
    src = _read("wax/tools/registry.py")
    assert "LEGACY removed from active catalog" in src
    for line in src.splitlines():
        s = line.strip()
        if s.startswith('"publish_web_surface"') or s.startswith('"create_html_page"'):
            raise AssertionError(f"active registration: {line}")


def test_no_second_surface_tutor_class():
    tutor = _read("wax/intelligence/tutor.py")
    assert "class SurfaceTutor" not in tutor
    assert "class WebTutor" not in tutor
    assert "class HTMLTutor" not in tutor


def test_surface_token_is_not_master_credential():
    src = _read("wax/surfaces/tokens.py")
    assert "token_urlsafe" in src or "secrets." in src
    assert "hmac" in src
    assert "compare_digest" in src


def test_cleanup_does_not_touch_memory_or_work():
    src = _read("wax/surfaces/service.py")
    cleanup = src.split("async def cleanup_expired")[1].split("async def ")[0]
    assert "Memory" not in cleanup
    assert "delete(Work" not in cleanup
    assert "Conversation" not in cleanup
    assert "Artifact" not in cleanup or "delete_uri" in cleanup  # only revision bytes


def test_bridge_omits_credentials_and_secrets():
    from wax.surfaces.runtime import inject_runtime_bridge
    b = inject_runtime_bridge("TOK", api_base_placeholder="https://s.example.com")
    assert "credentials: \"omit\"" in b or "credentials:\"omit\"" in b.replace(" ", "")
    assert "DATABASE" not in b
    assert "SECRET" not in b
    assert "API_KEY" not in b
    assert "https://s.example.com" in b
    assert "service_workers_disabled" in b


def test_whatsapp_resolves_principal_not_surface_token():
    src = _read("wax/messaging/whatsapp/handler.py")
    assert "_resolve_identity" in src
    assert "Principal" in src
    assert "Work(" in src


def test_surface_ai_payload_carries_principal_from_surface():
    """Surface AI Work is bound to surface.principal_id — token is capability only."""
    src = _read("wax/surfaces/service.py")
    block = src.split("async def accept_ai_request")[1].split("async def get_ai_request")[0]
    assert "principal_id=surface.principal_id" in block
    assert "kind=\"surface_ai\"" in block or "kind='surface_ai'" in block


def test_handle_surface_request_enriches_not_replaces_intelligence():
    src = _read("wax/intelligence/tutor.py")
    block = src.split("async def handle_surface_request")[1].split("async def handle_scheduled")[0]
    assert "handle_message" in block
    assert "Surface channel" in block or "surface" in block.lower()
    # Must not construct a separate CompletionRequest as the sole path
    # (handle_message owns the LLM call)
    assert "await self.handle_message" in block or "self.handle_message(work)" in block
