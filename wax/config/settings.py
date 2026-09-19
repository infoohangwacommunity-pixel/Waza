
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
    fallback_provider: str = "none"
    fallback_api_key: str = ""
    fallback_base_url: str = ""
    fallback_model: str = "gpt-4o-mini"
    fallback_timeout_seconds: float = 60.0
    memory_provider: str = "none"
    memory_api_key: str = ""
    memory_model: str = "gpt-4o-mini"
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
    terminal_timeout_seconds: int = 30
    terminal_max_output_bytes: int = 100_000
    terminal_workdir: str = "/tmp/wax-terminal"
    terminal_python: str = "python3"
    work_poll_interval_seconds: float = 1.0
    work_stale_seconds: int = 300
    work_max_retries: int = 3
    scheduler_poll_interval_seconds: float = 5.0
    delivery_max_retries: int = 5
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
    def is_production(self) -> bool:
        return self.app_env == "production"

@lru_cache
def get_settings() -> Settings:
    return Settings()
