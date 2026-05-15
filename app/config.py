"""Application configuration."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    app_env: str = "development"
    database_url: str
    storage_mode: str = "memory"
    daraja_mode: str = "mock"
    daraja_consumer_key: str | None = None
    daraja_consumer_secret: str | None = None
    daraja_passkey: str | None = None
    daraja_shortcode: str | None = None
    daraja_callback_url: str | None = None
    callback_shared_secret: str | None = None
    max_stk_amount: int = 10000

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""

    return Settings()  # type: ignore[call-arg]
