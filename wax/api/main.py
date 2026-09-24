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
                "Cache-Control": "private, max-age=60",
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

