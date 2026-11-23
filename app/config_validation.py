"""Startup configuration validation.

``validate_startup_settings`` is the single authoritative configuration gate. It is
called by every serving entry point (see ``app.startup.enforce_startup_safety``).

Messages name the offending setting and the rule it breaks. They never include the
configured value, so secrets cannot leak through exceptions, logs, or readiness output.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlparse

from redis import ConnectionPool

from app.config import Settings
from app.daraja.configuration import (
    CREDENTIAL_NAMES,
    OFFICIAL_DARAJA_BASE_URLS,
    legacy_setting_name,
    mode_setting_name,
    public_callback_host,
    resolve_credential,
    setting_value,
)
from app.deployment import DeploymentClassification, classify_deployment
from app.storage.database import create_database_engine

MIN_PRODUCTION_SECRET_LENGTH = 32
PLACEHOLDER_MARKERS = (
    "placeholder",
    "changeme",
    "change-me",
    "change_me",
    "example",
    "dummy",
    "your-",
    "local-callback-secret",
    "<",
    ">",
)
DEFAULT_DATABASE_PASSWORDS = frozenset({"mpesa", "pass", "password", "postgres", "changeme"})

APPROVAL_PERSISTENCE_ISSUE = "APPROVAL_PERSISTENCE"
"""Pseudo-setting for the durable-approval production blocker (not an env variable)."""


@dataclass(frozen=True)
class ConfigValidationIssue:
    """A single startup configuration validation issue."""

    setting: str
    reason: str


class StartupConfigValidationError(ValueError):
    """Raised when runtime configuration is unsafe or incomplete."""

    def __init__(self, issues: Iterable[ConfigValidationIssue]) -> None:
        self.issues: tuple[ConfigValidationIssue, ...] = tuple(issues)
        details = "; ".join(f"{issue.setting}: {issue.reason}" for issue in self.issues)
        super().__init__(f"Invalid startup configuration: {details}")

    @property
    def settings(self) -> tuple[str, ...]:
        """Return the names of the settings that failed validation."""

        return tuple(dict.fromkeys(issue.setting for issue in self.issues))


def validate_startup_settings(settings: Settings) -> None:
    """Validate settings that must be safe before runtime startup."""

    issues = collect_config_issues(settings)
    if issues:
        raise StartupConfigValidationError(issues)


def collect_config_issues(settings: Settings) -> list[ConfigValidationIssue]:
    """Return every configuration issue without raising."""

    classification = classify_deployment(settings)
    issues: list[ConfigValidationIssue] = [
        ConfigValidationIssue(item.setting, item.reason)
        for item in classification.contradictions
    ]
    issues.extend(_validate_mode_values(settings))
    issues.extend(_validate_daraja_production_settings(settings))
    issues.extend(_validate_daraja_credential_sources(settings, classification))
    issues.extend(_validate_identity_settings(settings))
    issues.extend(_validate_operator_auth_settings(settings))
    issues.extend(_validate_callback_secret_settings(settings, classification))
    issues.extend(_validate_callback_source_verification_settings(settings, classification))
    issues.extend(_validate_redis_settings(settings))
    issues.extend(_validate_postgres_settings(settings))
    if classification.requires_production_controls:
        issues.extend(_validate_production_controls(settings))
    return list(dict.fromkeys(issues))


def _validate_mode_values(settings: Settings) -> list[ConfigValidationIssue]:
    allowed: dict[str, tuple[str, frozenset[str]]] = {
        "STORAGE_MODE": (settings.storage_mode, frozenset({"memory", "postgres"})),
        "RATE_LIMIT_MODE": (settings.rate_limit_mode, frozenset({"memory", "redis"})),
        "CALLBACK_REPLAY_MODE": (settings.callback_replay_mode, frozenset({"memory", "redis"})),
        "PAYMENT_PROVIDER": (settings.payment_provider, frozenset({"daraja", "airtel_mock"})),
    }
    return [
        ConfigValidationIssue(name, "must be one of " + ", ".join(sorted(values)))
        for name, (value, values) in allowed.items()
        if value not in values
    ]


def _validate_daraja_production_settings(
    settings: Settings,
) -> list[ConfigValidationIssue]:
    """Validate everything the production Daraja client reads.

    Credentials are resolved with the same function the client uses, with no legacy
    fallback, so the validator checks exactly the values the runtime would send.
    """

    if settings.daraja_mode != "production":
        return []

    issues: list[ConfigValidationIssue] = []
    for name in CREDENTIAL_NAMES:
        resolved = resolve_credential(settings, name, allow_legacy=False)
        if resolved.value is None:
            issues.append(
                ConfigValidationIssue(resolved.setting, "required when DARAJA_MODE=production")
            )
        elif _looks_like_placeholder(resolved.value):
            issues.append(
                ConfigValidationIssue(
                    resolved.setting,
                    "must not be a placeholder value in production",
                )
            )

    callback_host = public_callback_host(settings.daraja_production_callback_url)
    if _has_value(settings.daraja_production_callback_url) and callback_host is None:
        issues.append(
            ConfigValidationIssue(
                "DARAJA_PRODUCTION_CALLBACK_URL",
                "must be a public https URL on a DNS name (no IP literal, port, userinfo, "
                "or internal or reserved host) in production",
            )
        )

    issues.extend(_validate_transaction_status_settings(settings, callback_host))
    return issues


def _validate_transaction_status_settings(
    settings: Settings,
    callback_host: str | None,
) -> list[ConfigValidationIssue]:
    """Validate the settings production Transaction Status queries send to Daraja.

    Transaction Status is always reachable in production (MCP tools and
    reconciliation), so these settings are required whenever DARAJA_MODE=production.
    Result and timeout destinations receive transaction data from Safaricom; they must
    be public https URLs on the same host as DARAJA_PRODUCTION_CALLBACK_URL, the single
    trusted callback ingress.
    """

    issues: list[ConfigValidationIssue] = []
    for name in ("DARAJA_INITIATOR_NAME", "DARAJA_SECURITY_CREDENTIAL"):
        value = setting_value(settings, name.lower())
        if value is None:
            issues.append(ConfigValidationIssue(name, "required when DARAJA_MODE=production"))
        elif _looks_like_placeholder(value):
            issues.append(
                ConfigValidationIssue(name, "must not be a placeholder value in production")
            )

    for name in ("DARAJA_TRANSACTION_STATUS_RESULT_URL", "DARAJA_TRANSACTION_STATUS_TIMEOUT_URL"):
        raw = getattr(settings, name.lower())
        if setting_value(settings, name.lower()) is None:
            issues.append(ConfigValidationIssue(name, "required when DARAJA_MODE=production"))
            continue
        host = public_callback_host(raw)
        if host is None:
            issues.append(
                ConfigValidationIssue(
                    name,
                    "must be a public https URL on a DNS name (no IP literal, port, "
                    "userinfo, or internal or reserved host) in production",
                )
            )
        elif callback_host is not None and host != callback_host:
            issues.append(
                ConfigValidationIssue(
                    name,
                    "must use the same host as DARAJA_PRODUCTION_CALLBACK_URL (the trusted "
                    "callback ingress)",
                )
            )
    return issues


def _validate_daraja_credential_sources(
    settings: Settings,
    classification: DeploymentClassification,
) -> list[ConfigValidationIssue]:
    """Reject implicit credential fallback outside local development.

    Legacy unprefixed DARAJA_* credentials are only used by the client in
    contradiction-free local development. Elsewhere, a legacy value that would have been
    used as a fallback is rejected, and production credentials must not be configured
    unless DARAJA_MODE=production.
    """

    if classification.allows_development_shortcuts:
        return []

    issues: list[ConfigValidationIssue] = []
    if settings.daraja_mode in OFFICIAL_DARAJA_BASE_URLS:
        for name in CREDENTIAL_NAMES:
            resolved = resolve_credential(settings, name, allow_legacy=False)
            if resolved.value is None and resolved.legacy_present:
                issues.append(
                    ConfigValidationIssue(
                        legacy_setting_name(name),
                        "legacy unprefixed credentials are only permitted in local "
                        f"development; set {mode_setting_name(settings.daraja_mode, name)}",
                    )
                )

    if settings.daraja_mode != "production":
        for name in CREDENTIAL_NAMES:
            if _has_value(getattr(settings, f"daraja_production_{name}", None)):
                issues.append(
                    ConfigValidationIssue(
                        mode_setting_name("production", name),
                        "production credentials must not be configured unless "
                        "DARAJA_MODE=production",
                    )
                )
    return issues


def _validate_operator_auth_settings(settings: Settings) -> list[ConfigValidationIssue]:
    if not settings.operator_auth_enabled or settings.auth_mode != "token":
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


def _validate_identity_settings(settings: Settings) -> list[ConfigValidationIssue]:
    if settings.auth_mode not in {"token", "oidc"}:
        return [
            ConfigValidationIssue(
                "AUTH_MODE",
                "must be one of token or oidc",
            )
        ]

    if settings.auth_mode != "oidc":
        return []

    issues: list[ConfigValidationIssue] = []
    if settings.oidc_provider_mode not in {"development", "jwks"}:
        issues.append(
            ConfigValidationIssue(
                "OIDC_PROVIDER_MODE",
                "must be one of development or jwks",
            )
        )

    required_settings = {
        "OIDC_ISSUER": settings.oidc_issuer,
        "OIDC_AUDIENCE": settings.oidc_audience,
    }
    issues.extend(
        ConfigValidationIssue(name, "required when AUTH_MODE=oidc")
        for name, value in required_settings.items()
        if not _has_value(value)
    )
    return issues


def _validate_callback_secret_settings(
    settings: Settings,
    classification: DeploymentClassification,
) -> list[ConfigValidationIssue]:
    if classification.allows_development_shortcuts:
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
    classification: DeploymentClassification,
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
        classification.requires_production_controls
        and settings.callback_source_verification_mode == "development"
    ):
        issues.append(
            ConfigValidationIssue(
                "CALLBACK_SOURCE_VERIFICATION_MODE",
                "development mode is not allowed for production-grade deployments "
                "(including DARAJA_MODE=production)",
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

    if not (
        _has_value(settings.redis_url)
        and settings.redis_url.startswith(("redis://", "rediss://"))
    ):
        return [
            ConfigValidationIssue(
                "REDIS_URL",
                "must be a redis:// or rediss:// URL when Redis-backed modes are enabled",
            )
        ]

    # Parse with the same constructor the runtime uses. This opens no connection; it
    # turns a malformed URL into a deterministic configuration error instead of a
    # failure during container construction. The exception text is never surfaced
    # because it can contain the URL, including credentials.
    try:
        ConnectionPool.from_url(settings.redis_url).disconnect()
    except Exception:
        return [ConfigValidationIssue("REDIS_URL", "must be a parseable Redis URL")]
    return []


def _validate_postgres_settings(settings: Settings) -> list[ConfigValidationIssue]:
    if settings.storage_mode != "postgres":
        return []

    if not (
        _has_value(settings.database_url)
        and settings.database_url.startswith(("postgresql://", "postgresql+", "postgres://"))
    ):
        return [
            ConfigValidationIssue(
                "DATABASE_URL",
                "must be a PostgreSQL URL when STORAGE_MODE=postgres",
            )
        ]

    # Build the engine exactly as the container does. SQLAlchemy engines connect
    # lazily, so this parses the URL and loads the dialect and driver without any
    # network access. The exception text is never surfaced because it can contain the
    # URL, including the password.
    try:
        create_database_engine(settings).dispose()
    except Exception:
        return [
            ConfigValidationIssue(
                "DATABASE_URL",
                "must be a parseable PostgreSQL URL with an installed driver",
            )
        ]
    return []


def _validate_production_controls(settings: Settings) -> list[ConfigValidationIssue]:
    """Return violations of production-grade controls.

    Applies whenever classification requires production controls: DARAJA_MODE=production,
    a production Daraja host, APP_ENV=production, or any unknown/contradictory value.
    """

    issues: list[ConfigValidationIssue] = []
    production = "for production-grade deployments"

    if not settings.operator_auth_enabled:
        issues.append(ConfigValidationIssue("OPERATOR_AUTH_ENABLED", f"must be true {production}"))

    if settings.auth_mode != "oidc":
        issues.append(
            ConfigValidationIssue(
                "AUTH_MODE",
                f"must be oidc {production}; shared static operator tokens are not supported",
            )
        )
    elif settings.oidc_provider_mode != "jwks":
        issues.append(
            ConfigValidationIssue(
                "OIDC_PROVIDER_MODE",
                f"must be jwks {production}; the development provider verifies no tokens",
            )
        )
    if _has_value(settings.oidc_issuer) and not _is_https_url(settings.oidc_issuer or ""):
        issues.append(ConfigValidationIssue("OIDC_ISSUER", f"must use https {production}"))

    for name, value in (
        ("OPERATOR_VIEWER_TOKEN", settings.operator_viewer_token),
        ("OPERATOR_APPROVER_TOKEN", settings.operator_approver_token),
        ("OPERATOR_ADMIN_TOKEN", settings.operator_admin_token),
    ):
        if _has_value(value):
            issues.append(
                ConfigValidationIssue(
                    name,
                    f"shared static operator credentials must not be configured {production}",
                )
            )

    if settings.storage_mode != "postgres":
        issues.append(ConfigValidationIssue("STORAGE_MODE", f"must be postgres {production}"))
    elif _uses_default_database_credentials(settings.database_url):
        issues.append(
            ConfigValidationIssue(
                "DATABASE_URL",
                f"must not use default development credentials {production}",
            )
        )

    # STORAGE_MODE=postgres persists transactions and audit events only. Approval
    # requests are always held in process-local memory, so no configuration can
    # currently provide durable, shared approvals. This blocker is intentional.
    issues.append(
        ConfigValidationIssue(
            APPROVAL_PERSISTENCE_ISSUE,
            "durable, shared approval persistence is not implemented (approvals are "
            "process-local even with STORAGE_MODE=postgres); production-grade "
            "deployments are blocked until it exists",
        )
    )

    if not settings.rate_limit_enabled:
        issues.append(ConfigValidationIssue("RATE_LIMIT_ENABLED", f"must be true {production}"))
    if settings.rate_limit_mode != "redis":
        issues.append(ConfigValidationIssue("RATE_LIMIT_MODE", f"must be redis {production}"))

    if not settings.callback_replay_protection_enabled:
        issues.append(
            ConfigValidationIssue(
                "CALLBACK_REPLAY_PROTECTION_ENABLED",
                f"must be true {production}",
            )
        )
    if settings.callback_replay_mode != "redis":
        issues.append(
            ConfigValidationIssue("CALLBACK_REPLAY_MODE", f"must be redis {production}")
        )

    if _has_value(settings.callback_shared_secret) and not _is_strong_secret(
        settings.callback_shared_secret
    ):
        issues.append(
            ConfigValidationIssue(
                "CALLBACK_SHARED_SECRET",
                f"must be at least {MIN_PRODUCTION_SECRET_LENGTH} characters and not a "
                f"placeholder {production}",
            )
        )
    if (
        settings.callback_source_verification_mode == "trusted_proxy"
        and _has_value(settings.trusted_proxy_shared_secret)
        and not _is_strong_secret(settings.trusted_proxy_shared_secret)
    ):
        issues.append(
            ConfigValidationIssue(
                "TRUSTED_PROXY_SHARED_SECRET",
                f"must be at least {MIN_PRODUCTION_SECRET_LENGTH} characters and not a "
                f"placeholder {production}",
            )
        )

    return issues


def _has_value(value: str | None) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _looks_like_placeholder(value: str | None) -> bool:
    lowered = (value or "").strip().lower()
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def _is_strong_secret(value: str | None) -> bool:
    candidate = (value or "").strip()
    return len(candidate) >= MIN_PRODUCTION_SECRET_LENGTH and not _looks_like_placeholder(
        candidate
    )


def _is_https_url(value: str) -> bool:
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return False
    return parsed.scheme == "https" and bool(parsed.hostname)


def _uses_default_database_credentials(database_url: str) -> bool:
    try:
        password = urlparse(database_url.strip()).password
    except ValueError:
        return True
    return password is not None and password.lower() in DEFAULT_DATABASE_PASSWORDS
