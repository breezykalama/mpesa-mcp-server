"""Application configuration."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    app_env: str = "development"
    database_url: str = "postgresql+asyncpg://mpesa:mpesa@localhost:5432/mpesa_mcp"
    storage_mode: str = "memory"
    payment_provider: str = "daraja"
    daraja_mode: str = "mock"
    daraja_sandbox_base_url: str = "https://sandbox.safaricom.co.ke"
    daraja_production_base_url: str = "https://api.safaricom.co.ke"
    daraja_consumer_key: str | None = None
    daraja_consumer_secret: str | None = None
    daraja_passkey: str | None = None
    daraja_shortcode: str | None = None
    daraja_callback_url: str | None = None
    daraja_sandbox_consumer_key: str | None = None
    daraja_sandbox_consumer_secret: str | None = None
    daraja_sandbox_passkey: str | None = None
    daraja_sandbox_shortcode: str | None = None
    daraja_sandbox_callback_url: str | None = None
    daraja_production_consumer_key: str | None = None
    daraja_production_consumer_secret: str | None = None
    daraja_production_passkey: str | None = None
    daraja_production_shortcode: str | None = None
    daraja_production_callback_url: str | None = None
    daraja_initiator_name: str | None = None
    daraja_security_credential: str | None = None
    daraja_transaction_status_result_url: str | None = None
    daraja_transaction_status_timeout_url: str | None = None
    daraja_identifier_type: int = 4
    daraja_transaction_status_remarks: str = "Transaction status query"
    daraja_transaction_status_occasion: str = "Mpesa MCP status check"
    daraja_request_timeout_seconds: float = 10.0
    daraja_max_retries: int = 2
    daraja_retry_backoff_seconds: float = 0.5
    daraja_circuit_breaker_enabled: bool = True
    daraja_circuit_breaker_failure_threshold: int = 5
    daraja_circuit_breaker_recovery_seconds: float = 60.0
    callback_shared_secret: str | None = None
    callback_source_verification_mode: str = "development"
    trusted_proxy_shared_secret: str | None = None
    trusted_proxy_header_name: str = "X-Trusted-Callback-Proxy"
    callback_replay_protection_enabled: bool = True
    callback_replay_window_seconds: int = 600
    callback_replay_mode: str = "memory"
    max_stk_amount: int = 10000
    rate_limit_enabled: bool = True
    rate_limit_mode: str = "memory"
    rate_limit_window_seconds: int = 60
    rate_limit_max_stk_push: int = 5
    rate_limit_max_approval_actions: int = 10
    rate_limit_max_status_checks: int = 30
    enabled_mcp_tools: str = ""
    blocked_mcp_tools: str = ""
    approval_required_mcp_tools: str = ""
    reconciliation_stale_pending_minutes: int = 15
    redis_url: str = "redis://localhost:6379/0"
    log_level: str = "INFO"
    log_format: str = "json"
    auth_mode: str = "token"
    operator_auth_enabled: bool = True
    operator_viewer_token: str | None = None
    operator_approver_token: str | None = None
    operator_admin_token: str | None = None
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_development_subject: str = "development-operator"
    oidc_development_email: str | None = "operator@example.test"
    oidc_development_display_name: str = "Development Operator"
    oidc_development_groups: str = ""
    oidc_viewer_groups: str = ""
    oidc_approver_groups: str = ""
    oidc_admin_groups: str = ""
    approval_expiry_minutes: int = 30
    approval_required_reviewers: int = 1
    high_risk_approval_required_reviewers: int = 2
    high_risk_amount_threshold: int = 50_000

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""

    return Settings()
