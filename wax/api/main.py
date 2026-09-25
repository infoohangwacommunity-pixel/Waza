"""
WAX Prep API — FastAPI application.

Webhook endpoints accept durably and return 200 only when accepted or duplicate.
Persistence failure → 5xx so providers retry.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse

from wax.config import get_settings, validate_production_settings
from wax.db.session import get_engine
from wax.observability.logging import get_logger, setup_logging

setup_logging()
logger = get_logger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_production_settings(settings)
    logger.info("wax_prep_starting", env=settings.app_env, version="0.2.0")
    yield
    engine = get_engine()
    await engine.dispose()
    logger.info("wax_prep_shutdown")


app = FastAPI(
    title="WAX Prep",
    description="The tutor that actually knows you.",
    version="0.2.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "service": "wax-prep", "env": settings.app_env, "version": "0.2.0"}


@app.get("/ready")
async def ready() -> JSONResponse:
    checks: dict[str, Any] = {"process": True}
    overall = True
    try:
        from sqlalchemy import text
        from wax.db.session import get_session_factory

        async with get_session_factory()() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception as e:
        checks["database"] = False
        checks["database_error"] = str(e)
        overall = False
    code = status.HTTP_200_OK if overall else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(status_code=code, content={"ready": overall, "checks": checks})


@app.get("/health/detail")
async def health_detail():
    from wax.terminal.workspace import workspace_root

    root = workspace_root()
    return {
        "status": "ok",
        "app": settings.app_name,
        "env": settings.app_env,
        "version": "0.3.0",
        "whatsapp_enabled": settings.whatsapp_enabled,
        "telegram_enabled": settings.telegram_enabled,
        "terminal_enabled": settings.terminal_enabled,
        "workspace_root": str(root),
        "primary_provider": settings.primary_provider,
        "fallback_provider": settings.fallback_provider,
        "storage_backend": getattr(settings, "effective_storage_backend", settings.storage_backend),
        "policy": {
            "allow_code_execution": getattr(settings, "allow_code_execution", True),
            "allow_external_network": getattr(settings, "allow_external_network", True),
            "allow_html_artifacts": getattr(settings, "allow_html_artifacts", True),
            "agent_max_tool_rounds": getattr(settings, "agent_max_tool_rounds", 8),
        },
        "capabilities": [
            "interactions",
            "schedule_series",
            "research_fetch",
            "learner_export",
            "html_pages",
            "work_leases",
        ],
    }


@app.get("/webhooks/whatsapp")
async def whatsapp_verify(request: Request) -> Response:
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if mode == "subscribe" and token and token == settings.whatsapp_verify_token:
        logger.info("whatsapp_webhook_verified")
        return Response(content=challenge or "", media_type="text/plain")
    return Response(status_code=status.HTTP_403_FORBIDDEN)


@app.post("/webhooks/whatsapp")
async def whatsapp_inbound(request: Request) -> JSONResponse:
    from wax.messaging.whatsapp.handler import WebhookAcceptError, handle_whatsapp_webhook

    body = await request.body()
    headers = dict(request.headers)
    try:
        result = await handle_whatsapp_webhook(body, headers)
        return JSONResponse(content=result, status_code=200)
    except WebhookAcceptError as e:
        logger.warning("whatsapp_accept_error", status=e.status, http=e.http_status)
        return JSONResponse(content={"status": e.status}, status_code=e.http_status)
    except Exception as e:
        logger.error("whatsapp_webhook_error", error=str(e))
        return JSONResponse(content={"status": "error"}, status_code=503)


@app.post("/webhooks/telegram")
async def telegram_inbound(request: Request) -> JSONResponse:
    from wax.messaging.telegram.handler import WebhookAcceptError, handle_telegram_webhook

    body = await request.body()
    headers = dict(request.headers)
    try:
        result = await handle_telegram_webhook(body, headers)
        return JSONResponse(content=result, status_code=200)
    except WebhookAcceptError as e:
        logger.warning("telegram_accept_error", status=e.status, http=e.http_status)
        return JSONResponse(content={"status": e.status}, status_code=e.http_status)
    except Exception as e:
        logger.error("telegram_webhook_error", error=str(e))
        return JSONResponse(content={"status": "error"}, status_code=503)


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "name": "WAX Prep",
        "motto": "The tutor that actually knows you.",
        "version": "0.2.0",
    }




@app.get("/artifacts/{artifact_id}/download")
async def artifact_download(artifact_id: str, token: str = ""):
    """Signed download — verifies ownership token; never exposes filesystem paths."""
    from uuid import UUID
    from fastapi.responses import Response
    from wax.db.models import Artifact
    from wax.db.session import session_scope
    from wax.artifacts.access import verify_download_token
    from wax.artifacts.storage import read_bytes

    try:
        aid = UUID(artifact_id)
    except Exception:
        return JSONResponse({"error": "invalid_id"}, status_code=400)
    async with session_scope() as session:
        art = await session.get(Artifact, aid)
        if not art:
            return JSONResponse({"error": "not_found"}, status_code=404)
        if not token or not verify_download_token(str(art.id), str(art.principal_id), token):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        uri = (art.structured or {}).get("storage_uri")
        if not uri:
            # Fallback: serve text content
            data = (art.content or "").encode("utf-8")
            ctype = art.content_type or "text/plain"
        else:
            try:
                data = read_bytes(uri)
            except Exception:
                return JSONResponse({"error": "storage_miss"}, status_code=404)
            ctype = art.content_type or "application/octet-stream"
        filename = (art.title or "artifact").replace('"', "")[:80]
        ext = "pdf" if "pdf" in (ctype or "") else "txt"
        return Response(
            content=data,
            media_type=ctype,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}.{ext}"',
                "Cache-Control": "private, max-age=300",
            },
        )


def run() -> None:
    import uvicorn

    uvicorn.run("wax.api.main:app", host="0.0.0.0", port=8000, reload=False)







def _surface_cors_headers(request: Request) -> dict:
    """Allow Surface browser origin to call the gateway without credentials."""
    from wax.config import get_settings
    s = get_settings()
    origin = request.headers.get("origin") or ""
    allowed = set()
    if s.public_base_url:
        allowed.add(s.public_base_url.rstrip("/"))
    so = (getattr(s, "surface_public_origin", None) or "").rstrip("/")
    if so:
        allowed.add(so)
    headers = {
        "Access-Control-Allow-Methods": "GET, PUT, PATCH, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Idempotency-Key",
        "Access-Control-Max-Age": "600",
    }
    if origin and origin.rstrip("/") in allowed:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Vary"] = "Origin"
    elif not origin:
        # non-browser or same-origin
        pass
    return headers

# ─── Web Surfaces (AI-authored interactive environments) ──────────────────────

@app.get("/s/{token}")
async def serve_surface(token: str):
    """Serve current revision of an AI-authored surface. No LLM on open."""
    from fastapi.responses import HTMLResponse, Response
    from wax.db.session import session_scope
    from wax.surfaces.service import SurfaceService
    from wax.surfaces.runtime import render_unavailable
    from wax.config import get_settings

    # Production fail-closed: refuse AI HTML without distinct Surface origin.
    _settings = get_settings()
    if (_settings.app_env or "") == "production":
        so = (getattr(_settings, "surface_public_origin", None) or "").rstrip("/")
        main = (_settings.public_base_url or "").rstrip("/")
        if not so or (main and so == main):
            html = render_unavailable(reason="failed")
            return HTMLResponse(
                content=html,
                status_code=503,
                headers={
                    "Cache-Control": "no-store",
                    "X-Robots-Tag": "noindex, nofollow, noarchive",
                    "X-WAX-Error": "origin_isolation_required",
                },
            )

    async with session_scope() as session:
        svc = SurfaceService(session)
        surface, deny = await svc.resolve_by_token(token)
        if deny or not surface:
            html = render_unavailable(reason=deny or "not_found")
            return HTMLResponse(
                content=html,
                status_code=410 if deny in ("expired", "revoked") else 404,
                headers={
                    "Cache-Control": "no-store",
                    "X-Robots-Tag": "noindex, nofollow, noarchive",
                    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
                    "X-Content-Type-Options": "nosniff",
                    "Referrer-Policy": "no-referrer",
                },
            )
        data = await svc.load_entry_html(surface, public_token=token)
        if not data:
            html = render_unavailable(reason="failed")
            return HTMLResponse(content=html, status_code=404, headers={"Cache-Control": "no-store"})
        try:
            await svc.record_access(surface)
            await session.commit()
        except Exception:
            pass
        # CSP allows scripts from self only (AI JS runs same-origin); no remote scripts.
        # connect-src limited to same origin for gateway.
        # connect-src: allow same origin and configured surface origin only.
        from wax.config import get_settings as _gs
        _s = _gs()
        connect_sources = ["'self'"]
        so = (getattr(_s, "surface_public_origin", None) or "").rstrip("/")
        if so:
            connect_sources.append(so)
        csp = (
            "default-src 'none'; "
            "script-src 'unsafe-inline'; "
            "style-src 'unsafe-inline'; "
            "img-src data: blob:; "
            "media-src data: blob:; "
            "font-src data:; "
            f"connect-src {' '.join(connect_sources)}; "
            "worker-src 'none'; "
            "child-src 'none'; "
            "manifest-src 'none'; "
            "frame-src 'none'; "
            "object-src 'none'; "
            "base-uri 'none'; "
            "form-action 'none'; "
            "frame-ancestors 'none'; "
            "navigate-to 'self'"
        )
        return Response(
            content=data,
            media_type="text/html; charset=utf-8",
            headers={
                "Cache-Control": "private, no-cache, must-revalidate",
                "X-Robots-Tag": "noindex, nofollow, noarchive",
                "Content-Security-Policy": csp,
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
                "X-Frame-Options": "DENY",
                "Cross-Origin-Opener-Policy": "same-origin",
                "Cross-Origin-Resource-Policy": "same-origin",
            },
        )



@app.options("/s/{token}/api/{path:path}")
async def surface_api_options(token: str, path: str, request: Request):
    from fastapi.responses import Response
    h = _surface_cors_headers(request)
    return Response(status_code=204, headers=h)

@app.get("/s/{token}/api/state")
async def surface_get_state(token: str, request: Request):
    from fastapi.responses import JSONResponse
    from wax.db.session import session_scope
    from wax.surfaces.service import SurfaceService
    from wax.surfaces.policy import CapabilityScope

    cors = _surface_cors_headers(request)
    async with session_scope() as session:
        svc = SurfaceService(session)
        surface, deny = await svc.resolve_by_token(token)
        if deny or not surface:
            return JSONResponse({"error": deny or "not_found"}, status_code=410 if deny else 404, headers=cors)
        if CapabilityScope.STATE_READ.value not in (surface.granted_scopes or []):
            return JSONResponse({"error": "forbidden"}, status_code=403, headers=cors)
        st = await svc.get_state(surface)
        return JSONResponse(
            {
                "ok": True,
                "state": st.get("state", st) if isinstance(st, dict) else st,
                "state_revision": st.get("state_revision", 0) if isinstance(st, dict) else 0,
                "revision": surface.current_revision,
            },
            headers=cors,
        )


@app.put("/s/{token}/api/state")
@app.patch("/s/{token}/api/state")
async def surface_set_state(token: str, request: Request):
    from fastapi.responses import JSONResponse
    from wax.db.session import session_scope
    from wax.surfaces.service import SurfaceService
    from wax.surfaces.policy import CapabilityScope

    cors = _surface_cors_headers(request)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400, headers=cors)
    async with session_scope() as session:
        svc = SurfaceService(session)
        surface, deny = await svc.resolve_by_token(token)
        if deny or not surface:
            return JSONResponse({"error": deny or "not_found"}, status_code=410 if deny else 404, headers=cors)
        if CapabilityScope.STATE_WRITE.value not in (surface.granted_scopes or []):
            return JSONResponse({"error": "forbidden"}, status_code=403, headers=cors)
        body = body if isinstance(body, dict) else {}
        expected_sr = body.pop("expected_state_revision", None)
        if request.method == "PATCH":
            # Allow {patch: {...}} or flat merge body
            patch = body.get("patch") if isinstance(body.get("patch"), dict) else body
            result = await svc.patch_state(
                surface, patch, expected_state_revision=expected_sr
            )
        else:
            state = body.get("state") if isinstance(body.get("state"), dict) else body
            result = await svc.set_state(
                surface, state, expected_state_revision=expected_sr
            )
        await session.commit()
        return JSONResponse(result, status_code=200 if result.get("ok") else 400, headers=cors)


@app.post("/s/{token}/api/events")
async def surface_post_event(token: str, request: Request):
    from fastapi.responses import JSONResponse
    from wax.db.session import session_scope
    from wax.surfaces.service import SurfaceService
    from wax.surfaces.policy import CapabilityScope

    cors = _surface_cors_headers(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    async with session_scope() as session:
        svc = SurfaceService(session)
        surface, deny = await svc.resolve_by_token(token)
        if deny or not surface:
            return JSONResponse({"error": deny or "not_found"}, status_code=410 if deny else 404, headers=cors)
        if CapabilityScope.EVENT_WRITE.value not in (surface.granted_scopes or []):
            return JSONResponse({"error": "forbidden"}, status_code=403, headers=cors)
        et = str((body or {}).get("type") or "interaction")[:80]
        payload = (body or {}).get("payload") if isinstance(body, dict) else {}
        if not isinstance(payload, dict):
            payload = {}
        if len(str(payload)) > 8000:
            payload = {"truncated": True}
        await svc.record_event(surface=surface, event_type=et, payload=payload)

        # Quiet web 👍/👎 — explicit interaction evidence only.
        # Principal is always derived from surface ownership (never browser-supplied).
        # Feedback must reference a response; never auto-promotes to durable preference.
        if et in ("response_feedback", "feedback"):
            from wax.config.settings import get_settings as _gs

            if getattr(_gs(), "web_feedback_enabled", True):
                try:
                    from wax.learner.signals import (
                        web_feedback_to_signal,
                        record_explicit_interaction_evidence,
                    )

                    direction = str(payload.get("direction") or payload.get("value") or "")
                    ref = payload.get("response_ref") or payload.get("message_id") or payload.get("work_id")
                    # Require a response reference so feedback is tied to a specific answer
                    if ref:
                        sig = web_feedback_to_signal(
                            direction,
                            response_ref=str(ref),
                            work_id=str(payload.get("work_id")) if payload.get("work_id") else None,
                            message_id=str(payload.get("message_id")) if payload.get("message_id") else None,
                        )
                        if sig.kind.value != "neutral":
                            await record_explicit_interaction_evidence(
                                session,
                                principal_id=surface.principal_id,  # from token ownership
                                signal=sig,
                                surface_id=surface.id,
                            )
                except Exception:
                    from wax.observability.logging import get_logger

                    get_logger(__name__).exception("web_feedback_signal_failed")

        await session.commit()
        return JSONResponse({"ok": True}, headers=cors)



@app.post("/s/{token}/api/ai")
async def surface_ai_request(token: str, request: Request):
    """Queue same-intelligence work. Returns opaque request_id, never work_id."""
    from fastapi.responses import JSONResponse
    from wax.db.session import session_scope
    from wax.surfaces.service import SurfaceService
    from wax.surfaces.policy import CapabilityScope

    cors = _surface_cors_headers(request)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400, headers=cors)
    message = str((body or {}).get("message") or "").strip()
    if not message or len(message) > 8000:
        return JSONResponse({"error": "invalid_message"}, status_code=400, headers=cors)
    idem = None
    if isinstance(body, dict):
        idem = body.get("idempotency_key") or request.headers.get("Idempotency-Key")

    async with session_scope() as session:
        svc = SurfaceService(session)
        surface, deny = await svc.resolve_by_token(token)
        if deny or not surface:
            return JSONResponse({"error": deny or "not_found"}, status_code=410 if deny else 404, headers=cors)
        if CapabilityScope.AI_REQUEST.value not in (surface.granted_scopes or []):
            return JSONResponse({"error": "forbidden"}, status_code=403, headers=cors)
        # Browser context is untrusted — only message string + opaque context bag.
        raw_ctx = (body or {}).get("context") if isinstance(body, dict) else {}
        if not isinstance(raw_ctx, dict):
            raw_ctx = {}
        # Strip any attempt to assert identity / internal ids
        for k in list(raw_ctx.keys()):
            if k in ("learner_id", "principal_id", "work_id", "surface_id", "token"):
                raw_ctx.pop(k, None)
        result = await svc.accept_ai_request(
            surface=surface,
            message=message,
            context=raw_ctx,
            idempotency_key=str(idem) if idem else None,
        )
        await session.commit()
        return JSONResponse(result, headers=cors)


@app.get("/s/{token}/api/ai/{request_id}")
async def surface_ai_status(token: str, request_id: str, request: Request):
    from fastapi.responses import JSONResponse
    from wax.db.session import session_scope
    from wax.surfaces.service import SurfaceService
    from wax.surfaces.policy import CapabilityScope

    cors = _surface_cors_headers(request)
    async with session_scope() as session:
        svc = SurfaceService(session)
        surface, deny = await svc.resolve_by_token(token)
        if deny or not surface:
            return JSONResponse({"error": deny or "not_found"}, status_code=410 if deny else 404, headers=cors)
        if CapabilityScope.AI_REQUEST.value not in (surface.granted_scopes or []):
            return JSONResponse({"error": "forbidden"}, status_code=403, headers=cors)
        row = await svc.get_ai_request_by_token(request_id, surface)
        if not row:
            return JSONResponse({"error": "not_found"}, status_code=404, headers=cors)
        return JSONResponse(
            {
                "ok": True,
                "status": row.status,
                "reply": row.reply_preview,
                "revision": surface.current_revision,
                "error": row.error,
            },
            headers=cors,
        )


@app.get("/s/{token}/api/revision")
async def surface_revision_poll(token: str, request: Request):
    """Let an open browser detect updates without exposing internals."""
    from fastapi.responses import JSONResponse
    from wax.db.session import session_scope
    from wax.surfaces.service import SurfaceService

    cors = _surface_cors_headers(request)
    headers = {"Cache-Control": "no-store", **cors}
    async with session_scope() as session:
        svc = SurfaceService(session)
        surface, deny = await svc.resolve_by_token(token)
        if deny or not surface:
            return JSONResponse({"error": deny or "not_found"}, status_code=410 if deny else 404, headers=headers)
        return JSONResponse(svc.revision_status(surface), headers=headers)


@app.get("/p/{token}")
async def serve_publication(token: str):
    """Serve immutable ephemeral publication by opaque public token."""
    from fastapi.responses import HTMLResponse, Response
    from wax.db.session import session_scope
    from wax.publication.service import PublicationService
    from wax.publication.renderer import render_expired_page

    async with session_scope() as session:
        svc = PublicationService(session)
        pub, deny = await svc.resolve_by_token(token)
        if deny or not pub:
            html = render_expired_page(reason=deny or "not_found")
            return HTMLResponse(
                content=html,
                status_code=410 if deny in ("expired", "revoked") else 404,
                headers={
                    "Cache-Control": "no-store",
                    "X-Robots-Tag": "noindex, nofollow",
                    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
                    "X-Content-Type-Options": "nosniff",
                    "Referrer-Policy": "no-referrer",
                },
            )
        data = await svc.load_html(pub)
        if not data:
            html = render_expired_page(reason="not_found")
            return HTMLResponse(content=html, status_code=404, headers={"Cache-Control": "no-store"})
        try:
            await svc.record_access(pub)
            await session.commit()
        except Exception:
            pass
        return Response(
            content=data,
            media_type="text/html; charset=utf-8",
            headers={
                "Cache-Control": "private, no-cache, must-revalidate",
                "X-Robots-Tag": "noindex, nofollow, noarchive",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data: https:; media-src https:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            },
        )


@app.get("/pages/{artifact_id}")
async def serve_html_page(artifact_id: str, token: str = ""):
    """Serve branded HTML artifact inline — signed token required (mini page)."""
    from uuid import UUID
    from fastapi.responses import HTMLResponse, Response
    from wax.db.models import Artifact
    from wax.db.session import session_scope
    from wax.artifacts.access import verify_download_token
    from wax.artifacts.storage import read_bytes

    try:
        aid = UUID(artifact_id)
    except Exception:
        return HTMLResponse("<h1>Invalid page</h1>", status_code=400)
    async with session_scope() as session:
        art = await session.get(Artifact, aid)
        if not art:
            return HTMLResponse("<h1>Not found</h1>", status_code=404)
        if not token or not verify_download_token(str(art.id), str(art.principal_id), token):
            return HTMLResponse("<h1>Link expired or invalid</h1>", status_code=403)
        try:
            uri = (art.structured or {}).get("storage_uri") or art.storage_path
            if not uri:
                return HTMLResponse("<h1>Page unavailable</h1>", status_code=404)
            data = read_bytes(uri)
        except Exception:
            return HTMLResponse("<h1>Page unavailable</h1>", status_code=404)
        ctype = art.content_type or "text/html; charset=utf-8"
        if "html" not in ctype and not data[:100].lstrip().lower().startswith(b"<!doctype") and not data[:50].lstrip().lower().startswith(b"<html"):
            # Non-HTML: force download instead
            return Response(
                content=data,
                media_type=ctype,
                headers={"Content-Disposition": f'attachment; filename="file"'},
            )
        return HTMLResponse(content=data.decode("utf-8", errors="replace"), status_code=200)

