"""Table-driven tests for the production configuration safety gate.

All credentials below are fake, non-placeholder test values. They are deliberately
distinctive so tests can prove they never appear in validation errors.
"""

from __future__ import annotations

import secrets
from typing import Any

import pytest
from app.config import Settings, allows_development_approval_authority
from app.config_validation import (
    APPROVAL_PERSISTENCE_ISSUE,
    StartupConfigValidationError,
    collect_config_issues,
    validate_startup_settings,
)
from app.deployment import DeploymentTier, classify_deployment


def _fake_secret(prefix: str) -> str:
    """Return a distinctive, high-entropy fake secret generated per test run.

    Sentinels are generated rather than written as literals so the repository never
    contains secret-shaped strings for the secret scanner to flag. Hex output cannot
    form any placeholder marker, so the hardened baseline stays valid.
    """

    return f"{prefix}-{secrets.token_hex(20)}"


SENTINEL_VALUES = {
    "callback_shared_secret": _fake_secret("cbsecret"),
    "trusted_proxy_shared_secret": _fake_secret("tpsecret"),
    "daraja_production_consumer_key": _fake_secret("ck"),
    "daraja_production_consumer_secret": _fake_secret("cs"),
    "daraja_production_passkey": _fake_secret("pk"),
    "daraja_security_credential": _fake_secret("sc"),
}
CALLBACK_HOST = "callbacks.mpesa-ops.co.ke"
DATABASE_PASSWORD = secrets.token_hex(12)
STATIC_TOKEN = "static-operator-token-8Hf3Kd9Lm2Qp7Rx4"
SECRET_STRINGS = (*SENTINEL_VALUES.values(), DATABASE_PASSWORD, STATIC_TOKEN)

HARDENED_PRODUCTION: dict[str, Any] = {
    "app_env": "production",
    "daraja_mode": "production",
    **SENTINEL_VALUES,
    "daraja_production_shortcode": "600000",
    "daraja_production_callback_url": f"https://{CALLBACK_HOST}/mpesa/stk",
    "daraja_initiator_name": "ops_initiator",
    "daraja_transaction_status_result_url": f"https://{CALLBACK_HOST}/mpesa/status/result",
    "daraja_transaction_status_timeout_url": f"https://{CALLBACK_HOST}/mpesa/status/timeout",
    "storage_mode": "postgres",
    "database_url": f"postgresql+psycopg://svc_mpesa:{DATABASE_PASSWORD}@db.internal.test:5432/x",
    "rate_limit_mode": "redis",
    "callback_replay_mode": "redis",
    "redis_url": "rediss://redis.internal.test:6380/0",
    "callback_source_verification_mode": "trusted_proxy",
    "operator_auth_enabled": True,
    "auth_mode": "oidc",
    "oidc_provider_mode": "jwks",
    "oidc_issuer": "https://login.microsoftonline.com/tenant-id/v2.0",
    "oidc_audience": "api-client-id",
}

MEMORY_DEFAULTS: dict[str, Any] = {
    "callback_shared_secret": SENTINEL_VALUES["callback_shared_secret"],
    "operator_auth_enabled": False,
}


def hardened(**overrides: Any) -> Settings:
    return Settings(**{**HARDENED_PRODUCTION, **overrides})


def rejected_settings(settings: Settings) -> tuple[str, ...]:
    with pytest.raises(StartupConfigValidationError) as exc_info:
        validate_startup_settings(settings)
    error = exc_info.value
    for secret in SECRET_STRINGS:
        assert secret not in str(error)
        assert secret not in repr(error.issues)
    return error.settings


# --- Durable approval blocker ---------------------------------------------------------


def test_fully_hardened_production_is_blocked_only_by_durable_approvals() -> None:
    assert rejected_settings(hardened()) == (APPROVAL_PERSISTENCE_ISSUE,)


def test_postgres_storage_is_not_treated_as_durable_approvals() -> None:
    settings = hardened(storage_mode="postgres")

    issues = collect_config_issues(settings)

    [blocker] = [issue for issue in issues if issue.setting == APPROVAL_PERSISTENCE_ISSUE]
    assert "process-local even with STORAGE_MODE=postgres" in blocker.reason


# --- Rejected configurations -----------------------------------------------------------

REJECTION_CASES: dict[str, tuple[dict[str, Any], set[str]]] = {
    "production-daraja-with-app-env-development": ({"app_env": "development"}, {"APP_ENV"}),
    "production-daraja-with-app-env-staging": ({"app_env": "staging"}, {"APP_ENV"}),
    "production-daraja-with-app-env-missing": ({"app_env": None}, {"APP_ENV"}),
    "production-daraja-auth-disabled": (
        {"operator_auth_enabled": False},
        {"OPERATOR_AUTH_ENABLED"},
    ),
    "production-daraja-development-oidc": (
        {"oidc_provider_mode": "development"},
        {"OIDC_PROVIDER_MODE"},
    ),
    "production-daraja-static-token-auth": (
        {"auth_mode": "token", "operator_approver_token": STATIC_TOKEN},
        {"AUTH_MODE", "OPERATOR_APPROVER_TOKEN"},
    ),
    "production-daraja-static-token-alongside-oidc": (
        {"operator_admin_token": STATIC_TOKEN},
        {"OPERATOR_ADMIN_TOKEN"},
    ),
    "production-daraja-memory-storage": ({"storage_mode": "memory"}, {"STORAGE_MODE"}),
    "production-daraja-default-database-credentials": (
        {"database_url": "postgresql+psycopg://mpesa:mpesa@db.internal.test:5432/x"},
        {"DATABASE_URL"},
    ),
    "production-daraja-memory-rate-limiting": (
        {"rate_limit_mode": "memory"},
        {"RATE_LIMIT_MODE"},
    ),
    "production-daraja-rate-limiting-disabled": (
        {"rate_limit_enabled": False},
        {"RATE_LIMIT_ENABLED"},
    ),
    "production-daraja-memory-replay-protection": (
        {"callback_replay_mode": "memory"},
        {"CALLBACK_REPLAY_MODE"},
    ),
    "production-daraja-replay-protection-disabled": (
        {"callback_replay_protection_enabled": False},
        {"CALLBACK_REPLAY_PROTECTION_ENABLED"},
    ),
    "production-daraja-development-callback-verification": (
        {"callback_source_verification_mode": "development"},
        {"CALLBACK_SOURCE_VERIFICATION_MODE"},
    ),
    "production-daraja-missing-callback-secret": (
        {"callback_shared_secret": None},
        {"CALLBACK_SHARED_SECRET"},
    ),
    "production-daraja-short-callback-secret": (
        {"callback_shared_secret": "short-secret"},
        {"CALLBACK_SHARED_SECRET"},
    ),
    "production-daraja-placeholder-callback-secret": (
        {"callback_shared_secret": "placeholder-" + "q" * 40},
        {"CALLBACK_SHARED_SECRET"},
    ),
    "production-daraja-weak-trusted-proxy-secret": (
        {"trusted_proxy_shared_secret": "proxy"},
        {"TRUSTED_PROXY_SHARED_SECRET"},
    ),
    "production-daraja-missing-consumer-key": (
        {"daraja_production_consumer_key": None},
        {"DARAJA_PRODUCTION_CONSUMER_KEY"},
    ),
    "production-daraja-placeholder-passkey": (
        {"daraja_production_passkey": "placeholder-passkey"},
        {"DARAJA_PRODUCTION_PASSKEY"},
    ),
    "production-daraja-http-callback-url": (
        {"daraja_production_callback_url": f"http://{CALLBACK_HOST}/stk"},
        {"DARAJA_PRODUCTION_CALLBACK_URL"},
    ),
    "production-daraja-localhost-callback-url": (
        {"daraja_production_callback_url": "https://localhost/stk"},
        {"DARAJA_PRODUCTION_CALLBACK_URL"},
    ),
    "production-daraja-http-base-url": (
        {"daraja_production_base_url": "http://api.safaricom.co.ke"},
        {"DARAJA_PRODUCTION_BASE_URL"},
    ),
    "production-http-oidc-issuer": (
        {"oidc_issuer": "http://login.microsoftonline.com/tenant-id/v2.0"},
        {"OIDC_ISSUER"},
    ),
    # --- M1: transaction-status settings read by the production client ---
    "production-missing-initiator": (
        {"daraja_initiator_name": None},
        {"DARAJA_INITIATOR_NAME"},
    ),
    "production-missing-security-credential": (
        {"daraja_security_credential": None},
        {"DARAJA_SECURITY_CREDENTIAL"},
    ),
    "production-placeholder-security-credential": (
        {"daraja_security_credential": "placeholder-credential"},
        {"DARAJA_SECURITY_CREDENTIAL"},
    ),
    "production-missing-result-url": (
        {"daraja_transaction_status_result_url": None},
        {"DARAJA_TRANSACTION_STATUS_RESULT_URL"},
    ),
    "production-http-result-url": (
        {"daraja_transaction_status_result_url": f"http://{CALLBACK_HOST}/status/result"},
        {"DARAJA_TRANSACTION_STATUS_RESULT_URL"},
    ),
    "production-private-ip-result-url": (
        {"daraja_transaction_status_result_url": "https://10.0.0.5/status/result"},
        {"DARAJA_TRANSACTION_STATUS_RESULT_URL"},
    ),
    "production-loopback-timeout-url": (
        {"daraja_transaction_status_timeout_url": "https://127.0.0.1/status/timeout"},
        {"DARAJA_TRANSACTION_STATUS_TIMEOUT_URL"},
    ),
    "production-link-local-timeout-url": (
        {"daraja_transaction_status_timeout_url": "https://169.254.169.254/latest"},
        {"DARAJA_TRANSACTION_STATUS_TIMEOUT_URL"},
    ),
    "production-internal-host-result-url": (
        {"daraja_transaction_status_result_url": "https://status.internal/result"},
        {"DARAJA_TRANSACTION_STATUS_RESULT_URL"},
    ),
    "production-port-result-url": (
        {"daraja_transaction_status_result_url": f"https://{CALLBACK_HOST}:8443/result"},
        {"DARAJA_TRANSACTION_STATUS_RESULT_URL"},
    ),
    "production-userinfo-timeout-url": (
        {"daraja_transaction_status_timeout_url": f"https://u:p@{CALLBACK_HOST}/timeout"},
        {"DARAJA_TRANSACTION_STATUS_TIMEOUT_URL"},
    ),
    "production-malformed-result-url": (
        {"daraja_transaction_status_result_url": "not a url"},
        {"DARAJA_TRANSACTION_STATUS_RESULT_URL"},
    ),
    "production-result-url-on-untrusted-host": (
        {"daraja_transaction_status_result_url": "https://results.attacker-ops.co.ke/r"},
        {"DARAJA_TRANSACTION_STATUS_RESULT_URL"},
    ),
    "production-private-ip-callback-url": (
        {"daraja_production_callback_url": "https://192.168.1.20/mpesa/stk"},
        {"DARAJA_PRODUCTION_CALLBACK_URL"},
    ),
    "production-legacy-credential-fallback": (
        {"daraja_production_passkey": None, "daraja_passkey": "legacy-passkey-value"},
        {"DARAJA_PRODUCTION_PASSKEY", "DARAJA_PASSKEY"},
    ),
    "production-non-official-endpoint": (
        {"daraja_production_base_url": "https://daraja-proxy.mpesa-ops.co.ke"},
        {"DARAJA_PRODUCTION_BASE_URL"},
    ),
    # --- Numeric-host callback destinations ---
    "production-octal-callback-host": (
        {
            "daraja_production_callback_url": "https://0300.0251.0326.0310/stk",
            "daraja_transaction_status_result_url": "https://0300.0251.0326.0310/r",
            "daraja_transaction_status_timeout_url": "https://0300.0251.0326.0310/t",
        },
        {
            "DARAJA_PRODUCTION_CALLBACK_URL",
            "DARAJA_TRANSACTION_STATUS_RESULT_URL",
            "DARAJA_TRANSACTION_STATUS_TIMEOUT_URL",
        },
    ),
    "production-leading-zero-result-url": (
        {"daraja_transaction_status_result_url": "https://192.168.1.020/status/result"},
        {"DARAJA_TRANSACTION_STATUS_RESULT_URL"},
    ),
    "production-short-form-timeout-url": (
        {"daraja_transaction_status_timeout_url": "https://127.1/status/timeout"},
        {"DARAJA_TRANSACTION_STATUS_TIMEOUT_URL"},
    ),
    "production-hex-callback-url": (
        {"daraja_production_callback_url": "https://0x7f000001/stk"},
        {"DARAJA_PRODUCTION_CALLBACK_URL"},
    ),
    # --- Malformed infrastructure URLs (parsed like the runtime, offline) ---
    "production-malformed-database-url": (
        {
            "database_url": (
                f"postgresql+psycopg://svc_mpesa:{DATABASE_PASSWORD}@db.internal.test:notaport/x"
            )
        },
        {"DATABASE_URL"},
    ),
    "production-unknown-database-driver": (
        {
            "database_url": (
                f"postgresql+nodriver://svc_mpesa:{DATABASE_PASSWORD}@db.internal.test/x"
            )
        },
        {"DATABASE_URL"},
    ),
    "production-malformed-redis-url": (
        {"redis_url": f"rediss://:{DATABASE_PASSWORD}@redis.internal.test:notaport/0"},
        {"REDIS_URL"},
    ),
    "unknown-daraja-mode": ({"daraja_mode": "live"}, {"DARAJA_MODE"}),
    "unknown-storage-mode": ({"storage_mode": "sqlite"}, {"STORAGE_MODE"}),
    "unknown-payment-provider": ({"payment_provider": "bank"}, {"PAYMENT_PROVIDER"}),
    "explicit-production-with-sandbox-daraja-and-memory": (
        {"daraja_mode": "sandbox", "storage_mode": "memory", "rate_limit_mode": "memory"},
        {"STORAGE_MODE", "RATE_LIMIT_MODE"},
    ),
}


@pytest.mark.parametrize("case", list(REJECTION_CASES))
def test_unsafe_production_configuration_is_rejected(case: str) -> None:
    overrides, expected = REJECTION_CASES[case]
    settings = hardened(**overrides)

    first = rejected_settings(settings)
    second = rejected_settings(settings)

    assert first == second, "validation must be deterministic"
    assert expected <= set(first), f"{case}: expected {expected}, got {first}"
    assert APPROVAL_PERSISTENCE_ISSUE in first
    assert classify_deployment(settings).requires_production_controls
    assert allows_development_approval_authority(settings) is False


ENVIRONMENT_CASES: dict[str, tuple[dict[str, Any], set[str]]] = {
    "explicit-production-mock-daraja-all-memory": (
        {"app_env": "production", "daraja_mode": "mock", **MEMORY_DEFAULTS},
        {
            "OPERATOR_AUTH_ENABLED",
            "AUTH_MODE",
            "STORAGE_MODE",
            "RATE_LIMIT_MODE",
            "CALLBACK_REPLAY_MODE",
            APPROVAL_PERSISTENCE_ISSUE,
        },
    ),
    "explicit-production-sandbox-daraja-all-memory": (
        {"app_env": "production", "daraja_mode": "sandbox", **MEMORY_DEFAULTS},
        {"OPERATOR_AUTH_ENABLED", "STORAGE_MODE", APPROVAL_PERSISTENCE_ISSUE},
    ),
    "unknown-app-env": (
        {"app_env": "qa", **MEMORY_DEFAULTS},
        {"APP_ENV", "OPERATOR_AUTH_ENABLED", APPROVAL_PERSISTENCE_ISSUE},
    ),
    "misspelled-production-app-env": (
        {"app_env": "prodution", **MEMORY_DEFAULTS},
        {"APP_ENV", APPROVAL_PERSISTENCE_ISSUE},
    ),
    "sandbox-daraja-with-app-env-missing": (
        {"daraja_mode": "sandbox", **MEMORY_DEFAULTS},
        {"APP_ENV"},
    ),
    "sandbox-mode-pointed-at-production-host": (
        {
            "app_env": "development",
            "daraja_mode": "sandbox",
            "daraja_sandbox_base_url": "https://api.safaricom.co.ke",
            **MEMORY_DEFAULTS,
        },
        {"DARAJA_SANDBOX_BASE_URL", "OPERATOR_AUTH_ENABLED", APPROVAL_PERSISTENCE_ISSUE},
    ),
}


@pytest.mark.parametrize("case", list(ENVIRONMENT_CASES))
def test_unsafe_environment_classification_is_rejected(case: str) -> None:
    kwargs, expected = ENVIRONMENT_CASES[case]
    settings = Settings(**kwargs)

    rejected = rejected_settings(settings)

    assert expected <= set(rejected), f"{case}: expected {expected}, got {rejected}"
    assert allows_development_approval_authority(settings) is False


# --- Permitted configurations ----------------------------------------------------------

PERMITTED_CASES: dict[str, tuple[dict[str, Any], DeploymentTier]] = {
    "explicit-local-development-mock-daraja": (
        {"app_env": "development", "daraja_mode": "mock", "operator_auth_enabled": False},
        DeploymentTier.DEVELOPMENT,
    ),
    "explicit-local-development-memory-components": (
        {
            "app_env": "local",
            "daraja_mode": "mock",
            "storage_mode": "memory",
            "rate_limit_mode": "memory",
            "callback_replay_mode": "memory",
            "callback_source_verification_mode": "development",
            "operator_auth_enabled": False,
        },
        DeploymentTier.DEVELOPMENT,
    ),
    "docker-compose-local-stack": (
        {
            "app_env": "development",
            "daraja_mode": "mock",
            "storage_mode": "postgres",
            "database_url": "postgresql+psycopg://mpesa:mpesa@postgres:5432/mpesa_mcp",
            "rate_limit_mode": "redis",
            "callback_replay_mode": "redis",
            "redis_url": "redis://redis:6379/0",
            "operator_auth_enabled": False,
        },
        DeploymentTier.DEVELOPMENT,
    ),
    "implicit-environment-mock-daraja-only": (
        {"daraja_mode": "mock", "operator_auth_enabled": False},
        DeploymentTier.DEVELOPMENT,
    ),
    "development-with-sandbox-daraja": (
        {"app_env": "development", "daraja_mode": "sandbox", "operator_auth_enabled": False},
        DeploymentTier.DEVELOPMENT,
    ),
    "staging-non-production-testing": (
        {"app_env": "staging", "daraja_mode": "sandbox", **MEMORY_DEFAULTS},
        DeploymentTier.NON_PRODUCTION,
    ),
}


@pytest.mark.parametrize("case", list(PERMITTED_CASES))
def test_explicitly_safe_configuration_starts(case: str) -> None:
    kwargs, tier = PERMITTED_CASES[case]
    settings = Settings(**kwargs)

    validate_startup_settings(settings)

    classification = classify_deployment(settings)
    assert classification.tier is tier
    assert classification.real_money_capable is False
    assert allows_development_approval_authority(settings) is (
        tier is DeploymentTier.DEVELOPMENT
    )


def test_documented_env_example_is_a_valid_local_development_config() -> None:
    settings = Settings(_env_file=".env.example")  # type: ignore[call-arg]

    validate_startup_settings(settings)
    assert classify_deployment(settings).tier is DeploymentTier.DEVELOPMENT


# --- Classification --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("app_env", "daraja_mode", "tier", "real_money"),
    [
        ("development", "mock", DeploymentTier.DEVELOPMENT, False),
        (" DEV ", "mock", DeploymentTier.DEVELOPMENT, False),
        ("test", "sandbox", DeploymentTier.DEVELOPMENT, False),
        (None, "mock", DeploymentTier.DEVELOPMENT, False),
        ("staging", "sandbox", DeploymentTier.NON_PRODUCTION, False),
        (None, "sandbox", DeploymentTier.NON_PRODUCTION, False),
        ("Production", "mock", DeploymentTier.PRODUCTION, False),
        ("prod", "sandbox", DeploymentTier.PRODUCTION, False),
        ("development", "production", DeploymentTier.PRODUCTION, True),
        (None, "production", DeploymentTier.PRODUCTION, True),
        ("qa", "mock", DeploymentTier.PRODUCTION, False),
        ("development", "live", DeploymentTier.PRODUCTION, False),
    ],
)
def test_deployment_classification(
    app_env: str | None,
    daraja_mode: str,
    tier: DeploymentTier,
    real_money: bool,
) -> None:
    classification = classify_deployment(Settings(app_env=app_env, daraja_mode=daraja_mode))

    assert classification.tier is tier
    assert classification.real_money_capable is real_money


def test_production_daraja_always_requires_production_controls_whatever_app_env() -> None:
    for app_env in (None, "development", "dev", "local", "test", "staging", "production"):
        classification = classify_deployment(
            Settings(app_env=app_env, daraja_mode="production")
        )
        assert classification.requires_production_controls, app_env
        assert not classification.allows_development_shortcuts, app_env


def test_production_daraja_host_in_sandbox_mode_is_real_money_capable() -> None:
    for url in ("https://api.safaricom.co.ke", "https://API.SAFARICOM.CO.KE/", "https://x.api.safaricom.co.ke"):
        classification = classify_deployment(
            Settings(app_env="development", daraja_mode="sandbox", daraja_sandbox_base_url=url)
        )
        assert classification.real_money_capable, url
        assert classification.tier is DeploymentTier.PRODUCTION, url
