"""Startup configuration validation."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings


class StartupConfigValidationError(ValueError):
    """Raised when runtime configuration is unsafe or incomplete."""


@dataclass(frozen=True)
class ConfigValidationIssue:
    """A single startup configuration validation issue."""

    setting: str
    reason: str


def validate_startup_settings(settings: Settings) -> None:
    """Validate settings that must be safe before runtime startup."""

    issues: list[ConfigValidationIssue] = []
    issues.extend(_validate_daraja_production_settings(settings))
    issues.extend(_validate_operator_auth_settings(settings))
    issues.extend(_validate_callback_secret_settings(settings))
    issues.extend(_validate_callback_source_verification_settings(settings))
    issues.extend(_validate_redis_settings(settings))
    issues.extend(_validate_postgres_settings(settings))

    if issues:
        details = "; ".join(f"{issue.setting}: {issue.reason}" for issue in issues)
        raise StartupConfigValidationError(f"Invalid startup configuration: {details}")


def _validate_daraja_production_settings(
    settings: Settings,
) -> list[ConfigValidationIssue]:
    if settings.daraja_mode != "production":
        return []

    required_settings = {
        "DARAJA_PRODUCTION_CONSUMER_KEY": settings.daraja_production_consumer_key,
        "DARAJA_PRODUCTION_CONSUMER_SECRET": settings.daraja_production_consumer_secret,
        "DARAJA_PRODUCTION_PASSKEY": settings.daraja_production_passkey,
        "DARAJA_PRODUCTION_SHORTCODE": settings.daraja_production_shortcode,
        "DARAJA_PRODUCTION_CALLBACK_URL": settings.daraja_production_callback_url,
    }
    return [
        ConfigValidationIssue(name, "required when DARAJA_MODE=production")
        for name, value in required_settings.items()
        if not _has_value(value)
    ]


def _validate_operator_auth_settings(settings: Settings) -> list[ConfigValidationIssue]:
    if not settings.operator_auth_enabled:
        return []

    if any(
        _has_value(token)
        for token in (
            settings.operator_viewer_token,
            settings.operator_approver_token,
            settings.operator_admin_token,
        )
    ):
        return []

    return [
        ConfigValidationIssue(
            "OPERATOR_AUTH_ENABLED",
            "at least one operator token is required when enabled",
        )
    ]


def _validate_callback_secret_settings(settings: Settings) -> list[ConfigValidationIssue]:
    if settings.app_env in {"development", "dev", "local", "test"}:
        return []

    if _has_value(settings.callback_shared_secret):
        return []

    return [
        ConfigValidationIssue(
            "CALLBACK_SHARED_SECRET",
            "required outside development environments",
        )
    ]


def _validate_callback_source_verification_settings(
    settings: Settings,
) -> list[ConfigValidationIssue]:
    allowed_modes = {"development", "trusted_proxy", "strict_block"}
    issues: list[ConfigValidationIssue] = []
    if settings.callback_source_verification_mode not in allowed_modes:
        issues.append(
            ConfigValidationIssue(
                "CALLBACK_SOURCE_VERIFICATION_MODE",
                "must be one of development, trusted_proxy, strict_block",
            )
        )
        return issues

    if (
        settings.daraja_mode == "production"
        and settings.callback_source_verification_mode == "development"
    ):
        issues.append(
            ConfigValidationIssue(
                "CALLBACK_SOURCE_VERIFICATION_MODE",
                "development mode is not allowed when DARAJA_MODE=production",
            )
        )

    if (
        settings.callback_source_verification_mode == "trusted_proxy"
        and not _has_value(settings.trusted_proxy_shared_secret)
    ):
        issues.append(
            ConfigValidationIssue(
                "TRUSTED_PROXY_SHARED_SECRET",
                "required when CALLBACK_SOURCE_VERIFICATION_MODE=trusted_proxy",
            )
        )

    return issues


def _validate_redis_settings(settings: Settings) -> list[ConfigValidationIssue]:
    redis_required = (
        settings.rate_limit_mode == "redis"
        or settings.callback_replay_mode == "redis"
    )
    if not redis_required:
        return []

    if _has_value(settings.redis_url) and settings.redis_url.startswith(
        ("redis://", "rediss://")
    ):
        return []

    return [
        ConfigValidationIssue(
            "REDIS_URL",
            "must be a redis:// or rediss:// URL when Redis-backed modes are enabled",
        )
    ]


def _validate_postgres_settings(settings: Settings) -> list[ConfigValidationIssue]:
    if settings.storage_mode != "postgres":
        return []

    if _has_value(settings.database_url) and settings.database_url.startswith(
        ("postgresql://", "postgresql+", "postgres://")
    ):
        return []

    return [
        ConfigValidationIssue(
            "DATABASE_URL",
            "must be a PostgreSQL URL when STORAGE_MODE=postgres",
        )
    ]


def _has_value(value: str | None) -> bool:
    return isinstance(value, str) and value.strip() != ""
