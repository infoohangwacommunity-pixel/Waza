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
    memory_max_retries: int = 1
    fallback_provider: str = "none"
    fallback_api_key: str = ""
    fallback_base_url: str = ""
    fallback_model: str = "gpt-4o-mini"
    fallback_timeout_seconds: float = 60.0
    memory_provider: str = "none"
    memory_api_key: str = ""
    memory_model: str = "gpt-4o-mini"
    memory_base_url: str = ""  # defaults to primary_base_url if empty
    embedding_model: str = "text-embedding-3-small"

    # Optional multimodal provider (same OpenAI-compatible shape) — only used when AI asks
    multimodal_provider: str = "none"
    multimodal_api_key: str = ""
    multimodal_base_url: str = ""
    multimodal_model: str = "gpt-4o-mini"

    whatsapp_enabled: bool = False
    whatsapp_verify_token: str = ""
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_api_version: str = "v21.0"
    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""

    terminal_enabled: bool = True
    terminal_timeout_seconds: int = 45
    terminal_max_output_bytes: int = 150_000
    terminal_workdir: str = "/tmp/wax-terminal"
    terminal_python: str = "python3"
    workspace_root: str = "/tmp/wax-workspaces"
    workspace_max_file_bytes: int = 25_000_000
    workspace_ttl_hours: int = 72
    storage_backend: str = "local"  # local | s3
    s3_bucket: str = ""
    s3_endpoint: str = ""
    s3_region: str = "auto"
    terminal_cpu_seconds: int = 20
    terminal_memory_bytes: int = 536870912
    terminal_require_sandbox: bool = False  # overridden true when app_env=production
    terminal_use_docker: bool = False  # prefer docker run --network none

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

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, v: str) -> str:
        if isinstance(v, str) and v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+asyncpg://", 1)
        return v

    @property
    def effective_terminal_require_sandbox(self) -> bool:
        """Production always requires real isolation (docker or bwrap)."""
        if self.app_env == "production":
            return True
        return bool(self.terminal_require_sandbox)

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
    if not s.terminal_require_sandbox:
        # Soft-enforce: document that production implies require
        # Actual refusal is in sandbox when app_env=production
        pass
    if problems:
        raise RuntimeError("Production configuration invalid: " + "; ".join(problems))
