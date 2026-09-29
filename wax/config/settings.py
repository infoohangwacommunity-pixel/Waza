"""Runtime configuration — only settings with live consumers.

Infrastructure safety (isolation, rate limits, retries) lives here.
AI teaching policy does not.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # Railway volumes are mounted per service. Web and worker each get their own
    # volume mount, but they must point at the same logical student World. The
    # canonical World tree lives on a shared external store (same volume family,
    # object storage, or NFS). Each service resolves its local mount via
    # WAX_SHARED_STORE_PATH and falls back to the existing WORKSPACE_ROOT path.
    shared_store_root: str = ""

    # --- App ---
    app_name: str = "WAX Prep"
    app_env: Literal["development", "staging", "production", "test"] = "development"
    log_level: str = "INFO"
    enable_structured_logging: bool = True
    secret_key: str = Field(default="change-me-in-production")
    public_base_url: str = ""
    # Optional dedicated Surface host; empty = same origin as public_base_url
    surface_public_origin: str = ""

    # --- Database ---
    database_url: str = "postgresql+asyncpg://wax:wax@localhost:5432/wax"
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_echo: bool = False

    # --- Intelligence (one path: messages → text; optional same-task fallback) ---
    primary_provider: str = "openai"
    primary_api_key: str = ""
    primary_base_url: str = ""
    primary_model: str = "gpt-4o-mini"
    primary_timeout_seconds: float = 60.0
    primary_max_retries: int = 2
    fallback_provider: str = "none"
    fallback_api_key: str = ""
    fallback_base_url: str = ""
    fallback_model: str = ""
    fallback_timeout_seconds: float = 60.0
    fallback_max_retries: int = 2
    provider_rate_limit_max_retries: int = 48

    # --- Messaging ---
    whatsapp_enabled: bool = False
    whatsapp_verify_token: str = ""
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_api_version: str = "v21.0"
    whatsapp_max_message_chars: int = 3500
    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    telegram_max_message_chars: int = 4000
    webhook_signature_required: bool = True

    # --- World / isolation (infrastructure safety) ---
    isolation_enabled: bool = True
    isolation_timeout_seconds: int = 45
    isolation_max_output_bytes: int = 150_000
    isolation_cpu_seconds: int = 20
    isolation_memory_bytes: int = 536870912
    isolation_require_sandbox: bool = False  # forced true in production via property
    isolation_use_docker: bool = False
    workspace_root: str = "/tmp/wax-workspaces"
    workspace_max_file_bytes: int = 25_000_000
    workspace_tmp_ttl_hours: int = 48  # tmp/cache scavenger only — never expires Worlds
    require_persistent_workspace: bool = False

    # --- Artifact storage ---
    storage_backend: str = "local"  # local | s3
    s3_bucket: str = ""
    s3_endpoint: str = ""
    s3_region: str = "auto"

    # --- Work / scheduler / delivery ---
    work_poll_interval_seconds: float = 1.0
    work_stale_seconds: int = 300
    scheduler_poll_interval_seconds: float = 5.0
    delivery_chunk_delay_seconds: float = 0.55

    # --- Rate protection (accepted messages are never discarded) ---
    rate_limit_enabled: bool = True
    rate_burst_capacity: int = 12
    rate_sustained_per_minute: int = 30
    rate_window_seconds: int = 60
    rate_cooldown_seconds: int = 15
    rate_queue_limit_per_principal: int = 40

    # --- Recovery / outage context ---
    recovery_batch_window_seconds: int = 90
    recovery_max_batch_size: int = 8
    recovery_poll_interval_seconds: float = 5.0
    outage_awareness_min_seconds: int = 30

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, v: str) -> str:
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
            if v.startswith("postgres://"):
                return "postgresql+asyncpg://" + v[len("postgres://") :]
            if v.startswith("postgresql://"):
                return "postgresql+asyncpg://" + v[len("postgresql://") :]
        return v

    @property
    def effective_storage_backend(self) -> str:
        import os

        explicit = (
            os.environ.get("WAX_STORAGE_BACKEND") or self.storage_backend or "local"
        ).lower()
        bucket = (os.environ.get("WAX_S3_BUCKET") or self.s3_bucket or "").strip()
        if explicit == "s3" or (self.app_env == "production" and bucket):
            return "s3" if bucket else "local"
        return explicit if explicit in ("local", "s3") else "local"

    @property
    def effective_workspace_root(self) -> str:
        import os

        shared = (
            os.environ.get("WAX_SHARED_STORE_PATH")
            or (self.shared_store_root or "").strip()
        )
        if shared:
            return shared
        legacy = os.environ.get("WAX_WORKSPACE_ROOT") or (self.workspace_root or "").strip()
        if legacy:
            return legacy
        return ""

    @property
    def effective_isolation_require_sandbox(self) -> bool:
        if self.app_env == "production":
            return True
        return bool(self.isolation_require_sandbox)

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def validate_production_settings(s: Settings | None = None) -> None:
    """Raise RuntimeError if production deploy is misconfigured."""
    s = s or get_settings()
    if s.app_env != "production":
        return
    problems: list[str] = []
    if not s.secret_key or s.secret_key in ("change-me-in-production", "change-me"):
        problems.append("SECRET_KEY must be set to a strong value")
    if "localhost" in (s.database_url or "") and s.database_url.endswith("@localhost:5432/wax"):
        problems.append("DATABASE_URL must not be the local default in production")
    if not s.primary_api_key and s.primary_provider not in ("none", ""):
        problems.append(
            "PRIMARY_API_KEY is required in production when a provider is configured"
        )
    root = (s.workspace_root or "").strip()
    if root.startswith("/tmp"):
        problems.append(
            "WORKSPACE_ROOT must not be under /tmp in production "
            "(use a durable volume, e.g. /data/wax-workspaces)"
        )
    if s.require_persistent_workspace and root.startswith("/tmp"):
        problems.append(
            "REQUIRE_PERSISTENT_WORKSPACE=true forbids ephemeral WORKSPACE_ROOT"
        )
    if not (s.public_base_url or "").strip():
        problems.append(
            "PUBLIC_BASE_URL must be set in production "
            "(canonical origin for the app and Surfaces)"
        )
    if problems:
        raise RuntimeError("Production configuration invalid: " + "; ".join(problems))
