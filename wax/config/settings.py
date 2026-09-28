from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_name: str = "WAX Prep"
    app_env: Literal["development", "staging", "production", "test"] = "development"
    debug: bool = False
    log_level: str = "INFO"
    secret_key: str = Field(default="change-me-in-production")
    public_base_url: str = ""  # e.g. https://web-production-xxx.up.railway.app
    # Distinct origin for AI-authored Surface HTML (real browser security principal).
    # When set (e.g. https://s.example.com), Surface public URLs and the runtime bridge
    # use this origin exclusively. Main app origin never serves untrusted Surface HTML
    # Optional dedicated Surface origin. When empty, Surfaces use public_base_url
    # (same Railway origin). Set only if you intentionally host Surfaces on another host.
    surface_public_origin: str = ""

    database_url: str = "postgresql+asyncpg://wax:wax@localhost:5432/wax"
    database_url_sync: str = "postgresql://wax:wax@localhost:5432/wax"
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_echo: bool = False

    primary_provider: str = "openai"
    primary_api_key: str = ""
    primary_base_url: str = ""
    primary_model: str = "gpt-4o-mini"
    primary_timeout_seconds: float = 60.0
    primary_max_retries: int = 2
    fallback_max_retries: int = 2
    fallback_provider: str = "none"
    fallback_api_key: str = ""
    fallback_base_url: str = ""
    fallback_model: str = "gpt-4o-mini"
    fallback_timeout_seconds: float = 60.0



    whatsapp_enabled: bool = False
    whatsapp_verify_token: str = ""
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_api_version: str = "v21.0"
    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""

    # Isolation resource bounds for World execution (single path; no terminal package)
    isolation_enabled: bool = True  # when false, world_exec refuses
    isolation_timeout_seconds: int = 45
    isolation_max_output_bytes: int = 150_000
    isolation_python: str = "python3"
    workspace_root: str = "/tmp/wax-workspaces"
    workspace_max_file_bytes: int = 25_000_000
    # TTL for tmp/cache only — never expires the student's durable World
    workspace_tmp_ttl_hours: int = 48
    # Deprecated alias; ignored for durable paths (see cleanup)
    workspace_ttl_hours: int = 48
    storage_backend: str = "local"  # local | s3
    s3_bucket: str = ""
    s3_endpoint: str = ""
    s3_region: str = "auto"
    isolation_cpu_seconds: int = 20
    isolation_memory_bytes: int = 536870912
    isolation_require_sandbox: bool = False  # overridden true when app_env=production
    isolation_use_docker: bool = False  # prefer docker run --network none

    allow_code_execution: bool = True
    allow_external_network: bool = True
    allow_html_artifacts: bool = True
    # When true in production, WORKSPACE_ROOT must exist and be writable (Railway Volume)
    require_persistent_workspace: bool = False
    work_poll_interval_seconds: float = 1.0
    work_stale_seconds: int = 300
    work_max_retries: int = 3
    scheduler_poll_interval_seconds: float = 5.0
    delivery_max_retries: int = 5
    delivery_pace_seconds: float = 0.55

    whatsapp_max_message_chars: int = 3500
    telegram_max_message_chars: int = 4000

    enable_structured_logging: bool = True
    webhook_signature_required: bool = True

    # --- Rate protection (burst-tolerant, not naive per-message) ---
    rate_limit_enabled: bool = True
    rate_burst_capacity: int = 12          # short burst of messages allowed
    rate_sustained_per_minute: int = 30    # sustained messages/min before throttle
    rate_window_seconds: int = 60
    rate_cooldown_seconds: int = 15
    rate_queue_limit_per_principal: int = 40

    # --- Recovery / outage ---
    recovery_batch_window_seconds: int = 90
    recovery_max_batch_size: int = 8
    recovery_poll_interval_seconds: float = 5.0
    outage_awareness_min_seconds: int = 30  # only surface outage context after this
    # Pure 429s do not burn work.max_attempts; this caps rate-limit-only retries
    provider_rate_limit_max_retries: int = 48
    delivery_chunk_delay_seconds: float = 0.55

    # --- Conversational check-in guardrails (AI decides moment; infra gates) ---
    checkin_min_messages: int = 25
    checkin_max_per_week: int = 2
    checkin_suppress_after_feedback_seconds: int = 300


    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, v: str) -> str:
        """Railway/Heroku provide postgres(ql):// — app + Alembic use asyncpg.

        Uses SQLAlchemy make_url so user/password/host are not corrupted by
        naive string replacement (e.g. passwords containing @ or :).
        """
        if not isinstance(v, str) or not v:
            return v
        if "+asyncpg" in v.split("://", 1)[0]:
            return v
        try:
            from sqlalchemy.engine import make_url

            u = make_url(v)
            base = u.drivername.split("+")[0]
            if base in ("postgres", "postgresql"):
                u = u.set(drivername="postgresql+asyncpg")
                return u.render_as_string(hide_password=False)
        except Exception:
            # Fallback for incomplete URLs during local tests
            if v.startswith("postgres://"):
                return "postgresql+asyncpg://" + v[len("postgres://") :]
            if v.startswith("postgresql://"):
                return "postgresql+asyncpg://" + v[len("postgresql://") :]
        return v

    def model_post_init(self, __context) -> None:
        """Apply WAX_LLM_* env aliases when PRIMARY_* is empty or placeholder.

        Railway configs often set WAX_LLM_API_KEY / BASE_URL / MODEL while the app
        reads PRIMARY_*. Map them once at load time so the worker authenticates.
        """
        import os

        def _blank_or_placeholder(v: str) -> bool:
            s = (v or "").strip()
            return s in (
                "",
                "REPLACE_WITH_YOUR_LLM_API_KEY",
                "change-me",
                "REPLACE_IF_USING_FALLBACK",
                "REPLACE_IF_SEPARATE_MEMORY_KEY",
            )

        wax_key = (os.environ.get("WAX_LLM_API_KEY") or "").strip()
        wax_base = (os.environ.get("WAX_LLM_BASE_URL") or "").strip()
        wax_model = (os.environ.get("WAX_LLM_MODEL") or "").strip()

        if _blank_or_placeholder(self.primary_api_key) and wax_key:
            object.__setattr__(self, "primary_api_key", wax_key)
        if not (self.primary_base_url or "").strip() and wax_base:
            object.__setattr__(self, "primary_base_url", wax_base)
        if wax_model and (
            not (self.primary_model or "").strip() or self.primary_model == "gpt-4o-mini"
        ):
            object.__setattr__(self, "primary_model", wax_model)

        fb_key = (os.environ.get("WAX_LLM_FALLBACK_1_API_KEY") or "").strip()
        if _blank_or_placeholder(self.fallback_api_key) and fb_key:
            object.__setattr__(self, "fallback_api_key", fb_key)
            if self.fallback_provider in ("none", "", "null"):
                object.__setattr__(self, "fallback_provider", "openai")

    @property
    def effective_storage_backend(self) -> str:
        """local for dev; s3 when bucket configured (required path for durable prod artifacts)."""
        import os
        explicit = (
            os.environ.get("WAX_STORAGE_BACKEND")
            or self.storage_backend
            or "local"
        ).lower()
        bucket = (
            os.environ.get("WAX_S3_BUCKET")
            or self.s3_bucket
            or ""
        ).strip()
        if explicit == "s3" or (self.app_env == "production" and bucket):
            return "s3" if bucket else "local"
        return explicit if explicit in ("local", "s3") else "local"

    @property
    def effective_isolation_require_sandbox(self) -> bool:
        """Production always requires real isolation (docker or bwrap)."""
        if self.app_env == "production":
            return True
        return bool(self.isolation_require_sandbox)

    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def validate_production_settings(settings: Settings | None = None) -> None:
    """Fail closed when APP_ENV=production and critical config is missing/unsafe."""
    s = settings or get_settings()
    if s.app_env != "production":
        return
    problems: list[str] = []
    if not s.secret_key or s.secret_key in ("change-me-in-production", "change-me"):
        problems.append("SECRET_KEY must be set to a strong value in production")
    if "localhost" in (s.database_url or "") and s.database_url.endswith("@localhost:5432/wax"):
        problems.append("DATABASE_URL appears to be the development default")
    if not s.primary_api_key and s.primary_provider not in ("none", ""):
        problems.append("PRIMARY_API_KEY is required in production when a provider is configured")
    # Terminal: production must not rely on rlimits-only
    # Force require_sandbox semantics (env may still set docker)
    if not s.isolation_require_sandbox:
        # Soft-enforce: document that production implies require
        # Actual refusal is in sandbox when app_env=production
        pass
    # Worlds must not live on ephemeral disk in production
    root = (s.workspace_root or "").strip()
    if root.startswith("/tmp"):
        problems.append(
            "WORKSPACE_ROOT must not be under /tmp in production "
            "(use a durable volume, e.g. /data/wax-workspaces)"
        )
    if s.require_persistent_workspace and root.startswith("/tmp"):
        problems.append("REQUIRE_PERSISTENT_WORKSPACE=true forbids ephemeral WORKSPACE_ROOT")
    # Surfaces share PUBLIC_BASE_URL by default (same Railway origin).
    # SURFACE_PUBLIC_ORIGIN is optional for advanced multi-host deployments only.
    main = (s.public_base_url or "").strip().rstrip("/")
    if not main:
        problems.append(
            "PUBLIC_BASE_URL must be set in production "
            "(canonical origin for the app and Surfaces, e.g. https://your-app.up.railway.app)"
        )
    if problems:
        raise RuntimeError("Production configuration invalid: " + "; ".join(problems))
