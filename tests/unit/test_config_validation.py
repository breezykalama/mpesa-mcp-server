"""Tests for startup configuration validation."""

from __future__ import annotations

import pytest
from app.config import Settings
from app.config_validation import (
    StartupConfigValidationError,
    validate_startup_settings,
)


def test_valid_development_config_passes() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://user:pass@localhost:5432/test",
        app_env="development",
        operator_auth_enabled=False,
    )

    validate_startup_settings(settings)


def test_production_mode_missing_credentials_fails() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://user:pass@localhost:5432/test",
        daraja_mode="production",
        operator_auth_enabled=False,
    )

    with pytest.raises(StartupConfigValidationError) as exc_info:
        validate_startup_settings(settings)

    assert "DARAJA_PRODUCTION_CONSUMER_KEY" in str(exc_info.value)
    assert "DARAJA_PRODUCTION_CALLBACK_URL" in str(exc_info.value)


def test_production_mode_with_credentials_passes() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://user:pass@localhost:5432/test",
        daraja_mode="production",
        operator_auth_enabled=False,
        daraja_production_consumer_key="consumer-key",
        daraja_production_consumer_secret="consumer-secret",
        daraja_production_passkey="passkey",
        daraja_production_shortcode="174379",
        daraja_production_callback_url="https://example.test/callback",
        callback_source_verification_mode="trusted_proxy",
        trusted_proxy_shared_secret="trusted-proxy-secret",
    )

    validate_startup_settings(settings)


def test_redis_mode_missing_config_fails() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://user:pass@localhost:5432/test",
        operator_auth_enabled=False,
        rate_limit_mode="redis",
        redis_url="",
    )

    with pytest.raises(StartupConfigValidationError) as exc_info:
        validate_startup_settings(settings)

    assert "REDIS_URL" in str(exc_info.value)


def test_postgres_mode_missing_config_fails() -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        operator_auth_enabled=False,
        storage_mode="postgres",
    )

    with pytest.raises(StartupConfigValidationError) as exc_info:
        validate_startup_settings(settings)

    assert "DATABASE_URL" in str(exc_info.value)


def test_operator_auth_invalid_config_fails() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://user:pass@localhost:5432/test",
        operator_auth_enabled=True,
        operator_viewer_token=None,
        operator_approver_token=None,
        operator_admin_token=None,
    )

    with pytest.raises(StartupConfigValidationError) as exc_info:
        validate_startup_settings(settings)

    assert "OPERATOR_AUTH_ENABLED" in str(exc_info.value)


def test_operator_auth_with_any_token_passes() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://user:pass@localhost:5432/test",
        operator_auth_enabled=True,
        operator_admin_token="local-admin-token",
    )

    validate_startup_settings(settings)


def test_oidc_mode_missing_config_fails() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://user:pass@localhost:5432/test",
        auth_mode="oidc",
        operator_auth_enabled=True,
        oidc_issuer=None,
        oidc_audience=None,
    )

    with pytest.raises(StartupConfigValidationError) as exc_info:
        validate_startup_settings(settings)

    assert "OIDC_ISSUER" in str(exc_info.value)
    assert "OIDC_AUDIENCE" in str(exc_info.value)


def test_oidc_mode_with_required_config_passes() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://user:pass@localhost:5432/test",
        auth_mode="oidc",
        operator_auth_enabled=True,
        oidc_issuer="https://identity.example.test",
        oidc_audience="mpesa-operator-api",
    )

    validate_startup_settings(settings)


def test_callback_secret_required_outside_development() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://user:pass@localhost:5432/test",
        app_env="production",
        operator_auth_enabled=False,
        callback_shared_secret="",
    )

    with pytest.raises(StartupConfigValidationError) as exc_info:
        validate_startup_settings(settings)

    assert "CALLBACK_SHARED_SECRET" in str(exc_info.value)


def test_production_daraja_disallows_development_callback_source_verifier() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://user:pass@localhost:5432/test",
        app_env="development",
        daraja_mode="production",
        operator_auth_enabled=False,
        daraja_production_consumer_key="consumer-key",
        daraja_production_consumer_secret="consumer-secret",
        daraja_production_passkey="passkey",
        daraja_production_shortcode="174379",
        daraja_production_callback_url="https://example.test/callback",
        callback_source_verification_mode="development",
    )

    with pytest.raises(StartupConfigValidationError) as exc_info:
        validate_startup_settings(settings)

    assert "development mode is not allowed" in str(exc_info.value)


def test_trusted_proxy_missing_config_fails_startup_validation() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://user:pass@localhost:5432/test",
        operator_auth_enabled=False,
        callback_source_verification_mode="trusted_proxy",
        trusted_proxy_shared_secret="",
    )

    with pytest.raises(StartupConfigValidationError) as exc_info:
        validate_startup_settings(settings)

    assert "TRUSTED_PROXY_SHARED_SECRET" in str(exc_info.value)


def test_trusted_proxy_valid_config_passes_startup_validation() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://user:pass@localhost:5432/test",
        operator_auth_enabled=False,
        callback_source_verification_mode="trusted_proxy",
        trusted_proxy_shared_secret="trusted-proxy-secret",
    )

    validate_startup_settings(settings)
