"""
WAX Prep API — FastAPI application.

Webhook endpoints acknowledge quickly and enqueue durable work.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse

from wax.config import get_settings
from wax.db.session import get_engine
from wax.observability.logging import get_logger, setup_logging

setup_logging()
logger = get_logger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("wax_prep_starting", env=settings.app_env)
    yield
    engine = get_engine()
    await engine.dispose()
    logger.info("wax_prep_shutdown")


app = FastAPI(
    title="WAX Prep",
    description="The tutor that actually knows you.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "service": "wax-prep", "env": settings.app_env}


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
    from wax.messaging.whatsapp.handler import handle_whatsapp_webhook
    from wax.security.webhooks import verify_whatsapp_signature

    body = await request.body()
    headers = dict(request.headers)
    if settings.webhook_signature_required and settings.whatsapp_app_secret:
        sig = headers.get("x-hub-signature-256") or headers.get("X-Hub-Signature-256")
        if not verify_whatsapp_signature(settings.whatsapp_app_secret, body, sig):
            logger.warning("whatsapp_signature_invalid")
            return JSONResponse(content={"status": "invalid_signature"}, status_code=403)
    try:
        result = await handle_whatsapp_webhook(body, headers)
        return JSONResponse(content=result, status_code=200)
    except Exception as e:
        logger.error("whatsapp_webhook_error", error=str(e))
        # Always 200 to WhatsApp after accept-path errors to avoid endless retries on our bugs
        return JSONResponse(content={"status": "error"}, status_code=200)


@app.post("/webhooks/telegram")
async def telegram_inbound(request: Request) -> JSONResponse:
    from wax.messaging.telegram.handler import handle_telegram_webhook

    body = await request.body()
    headers = dict(request.headers)
    try:
        result = await handle_telegram_webhook(body, headers)
        return JSONResponse(content=result, status_code=200)
    except Exception as e:
        logger.error("telegram_webhook_error", error=str(e))
        return JSONResponse(content={"status": "error"}, status_code=200)


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "name": "WAX Prep",
        "motto": "The tutor that actually knows you.",
        "docs": "/docs",
    }


def run() -> None:
    import uvicorn
    uvicorn.run("wax.api.main:app", host="0.0.0.0", port=8000, reload=False)


@app.get("/health/detail")
async def health_detail():
    from wax.config import get_settings
    from wax.terminal.workspace import workspace_root
    s = get_settings()
    root = workspace_root()
    return {
        "status": "ok",
        "app": s.app_name,
        "env": s.app_env,
        "whatsapp_enabled": s.whatsapp_enabled,
        "telegram_enabled": s.telegram_enabled,
        "terminal_enabled": s.terminal_enabled,
        "workspace_root": str(root),
        "primary_provider": s.primary_provider,
        "fallback_provider": s.fallback_provider,
    }

