"""Application configuration."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    app_env: str = "development"
    database_url: str = "postgresql+asyncpg://mpesa:mpesa@localhost:5432/mpesa_mcp"
    storage_mode: str = "memory"
    daraja_mode: str = "mock"
    daraja_consumer_key: str | None = None
    daraja_consumer_secret: str | None = None
    daraja_passkey: str | None = None
    daraja_shortcode: str | None = None
    daraja_callback_url: str | None = None
    daraja_initiator_name: str | None = None
    daraja_security_credential: str | None = None
    daraja_transaction_status_result_url: str | None = None
    daraja_transaction_status_timeout_url: str | None = None
    daraja_identifier_type: int = 4
    daraja_transaction_status_remarks: str = "Transaction status query"
    daraja_transaction_status_occasion: str = "Mpesa MCP status check"
    callback_shared_secret: str | None = None
    max_stk_amount: int = 10000
    rate_limit_enabled: bool = True
    rate_limit_mode: str = "memory"
    rate_limit_window_seconds: int = 60
    rate_limit_max_stk_push: int = 5
    rate_limit_max_approval_actions: int = 10
    rate_limit_max_status_checks: int = 30
    redis_url: str = "redis://localhost:6379/0"
    log_level: str = "INFO"
    log_format: str = "json"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""

    return Settings()
