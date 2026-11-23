"""Tests for the Daraja endpoint trust policy and credential resolution.

No test performs a network call: HTTP is served by ``httpx.MockTransport``.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from app.config import Settings, allows_development_approval_authority
from app.config_validation import collect_config_issues
from app.daraja.client import RealDarajaClient
from app.daraja.configuration import (
    OFFICIAL_DARAJA_BASE_URLS,
    canonical_https_origin,
    is_acceptable_dns_hostname,
    public_callback_host,
    resolve_base_url,
    resolve_credential,
    trusted_base_url,
)
from app.deployment import DeploymentTier, classify_deployment

SANDBOX = OFFICIAL_DARAJA_BASE_URLS["sandbox"]
PRODUCTION = OFFICIAL_DARAJA_BASE_URLS["production"]


# --- Canonicalization ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://sandbox.safaricom.co.ke", SANDBOX),
        ("https://sandbox.safaricom.co.ke/", SANDBOX),
        ("  https://SANDBOX.Safaricom.CO.KE  ", SANDBOX),
        ("https://api.safaricom.co.ke", PRODUCTION),
        ("https://daraja-gateway.internal.test", "https://daraja-gateway.internal.test"),
        ("https://api.safaricom.co.ke.evil.test", "https://api.safaricom.co.ke.evil.test"),
        ("https://xn--api-safaricom.co.ke", "https://xn--api-safaricom.co.ke"),
        ("https://api.safaricom.co.ke.", None),
        ("https://196.201.214.200", None),
        ("https://[2001:db8::1]", None),
        ("https://sandbox.safaricom.co.ke:443", None),
        ("https://sandbox.safaricom.co.ke:8443", None),
        ("https://user@sandbox.safaricom.co.ke", None),
        ("https://sandbox.safaricom.co.ke@api.safaricom.co.ke", None),
        ("https://sandbox.safaricom.co.ke\\@api.safaricom.co.ke", None),
        ("https://sandbox.safaricom.co.ke%2e", None),
        ("https://sandbox%2Esafaricom.co.ke", None),
        ("http://sandbox.safaricom.co.ke", None),
        ("ftp://sandbox.safaricom.co.ke", None),
        ("sandbox.safaricom.co.ke", None),
        ("//sandbox.safaricom.co.ke", None),
        ("https://sandbox.safaricom.co.ke/v1", None),
        ("https://sandbox.safaricom.co.ke?x=1", None),
        ("https://sandbox.safaricom.co.ke#frag", None),
        ("https://sandbox .safaricom.co.ke", None),
        ("https://", None),
        ("", None),
    ],
)
def test_canonical_https_origin(url: str, expected: str | None) -> None:
    assert canonical_https_origin(url) == expected


# --- Classification of configured endpoints ---------------------------------------------

UNTRUSTED_SANDBOX_URLS = (
    "https://api.safaricom.co.ke",
    "https://api.safaricom.co.ke.",
    "https://196.201.214.200",
    "https://daraja-gateway.internal.test",
    "https://sandbox.safaricom.co.ke:8443",
    "https://user@sandbox.safaricom.co.ke",
    "http://sandbox.safaricom.co.ke",
    "ftp://sandbox.safaricom.co.ke",
    "sandbox.safaricom.co.ke",
    "https://sandbox.safaricom.co.ke.attacker.test",
    "https://sandbox.safaricom.co.ke%2e",
)


@pytest.mark.parametrize("url", UNTRUSTED_SANDBOX_URLS)
def test_untrusted_sandbox_endpoint_never_gets_development_authority(url: str) -> None:
    settings = Settings(
        app_env="development",
        daraja_mode="sandbox",
        daraja_sandbox_base_url=url,
        operator_auth_enabled=False,
        operator_approver_token="static-approver-token",
    )

    classification = classify_deployment(settings)

    assert classification.real_money_capable is True
    assert classification.tier is DeploymentTier.PRODUCTION
    assert allows_development_approval_authority(settings) is False
    assert "DARAJA_SANDBOX_BASE_URL" in {issue.setting for issue in collect_config_issues(settings)}
    with pytest.raises(ValueError, match="DARAJA_SANDBOX_BASE_URL is not a trusted"):
        RealDarajaClient(settings=settings)


@pytest.mark.parametrize(
    "url",
    ["https://sandbox.safaricom.co.ke", "https://daraja-proxy.mpesa-ops.co.ke", "http://api.safaricom.co.ke"],
)
def test_production_mode_requires_the_official_production_endpoint(url: str) -> None:
    settings = Settings(
        app_env="production",
        daraja_mode="production",
        daraja_production_base_url=url,
    )

    assert "DARAJA_PRODUCTION_BASE_URL" in {
        issue.setting for issue in collect_config_issues(settings)
    }
    assert trusted_base_url(settings) is None


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("sandbox", SANDBOX), ("production", PRODUCTION)],
)
def test_validator_and_runtime_agree_on_official_endpoint(mode: str, expected: str) -> None:
    settings = Settings(
        app_env="development" if mode == "sandbox" else "production",
        daraja_mode=mode,
        daraja_sandbox_base_url="https://SANDBOX.safaricom.co.ke/",
        daraja_production_base_url="https://API.safaricom.co.ke/",
        daraja_production_consumer_key="ck",
        daraja_production_consumer_secret="cs",
        daraja_production_passkey="pk",
        daraja_production_shortcode="600000",
        daraja_production_callback_url="https://callbacks.mpesa-ops.co.ke/stk",
    )

    assert trusted_base_url(settings) == expected
    assert resolve_base_url(settings) == expected
    assert RealDarajaClient(settings=settings)._base_url == expected


def test_client_has_no_endpoint_override() -> None:
    with pytest.raises(TypeError):
        RealDarajaClient(  # type: ignore[call-arg]
            settings=Settings(app_env="development", daraja_mode="sandbox"),
            base_url="https://daraja-gateway.internal.test",
        )


# --- Redirects ---------------------------------------------------------------------------


def test_default_http_client_does_not_follow_redirects() -> None:
    client = RealDarajaClient(settings=Settings(app_env="development", daraja_mode="sandbox"))

    assert client._http_client.follow_redirects is False


def test_redirect_to_production_host_is_not_followed() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"Location": f"{PRODUCTION}/oauth/v1/generate"})

    settings = Settings(
        app_env="development",
        daraja_mode="sandbox",
        daraja_sandbox_consumer_key="ck",
        daraja_sandbox_consumer_secret="cs",
        daraja_sandbox_passkey="pk",
        daraja_sandbox_shortcode="174379",
        daraja_sandbox_callback_url="https://callbacks.mpesa-ops.co.ke/stk",
        daraja_initiator_name="testapi",
        daraja_security_credential="credential",
        daraja_transaction_status_result_url="https://callbacks.mpesa-ops.co.ke/result",
        daraja_transaction_status_timeout_url="https://callbacks.mpesa-ops.co.ke/timeout",
        daraja_max_retries=0,
    )
    client = RealDarajaClient(
        settings=settings,
        http_client=httpx.Client(
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
        ),
    )

    response = client.check_transaction_status("LKXXXX1234")

    assert response.status == "failed"
    assert [request.url.host for request in requests] == ["sandbox.safaricom.co.ke"]


# --- Credential resolution ----------------------------------------------------------------

LEGACY: dict[str, Any] = {
    "daraja_consumer_key": "legacy-consumer-key",
    "daraja_consumer_secret": "legacy-consumer-secret",
    "daraja_passkey": "legacy-passkey",
    "daraja_shortcode": "174379",
    "daraja_callback_url": "https://callbacks.mpesa-ops.co.ke/stk",
}


def test_legacy_credentials_are_used_only_in_local_development() -> None:
    development = Settings(app_env="development", daraja_mode="sandbox", **LEGACY)

    client = RealDarajaClient(settings=development)

    assert client._required_credential("consumer_key") == "legacy-consumer-key"
    assert "DARAJA_CONSUMER_KEY" not in {i.setting for i in collect_config_issues(development)}


def test_legacy_credentials_are_rejected_in_staging_by_validator_and_runtime() -> None:
    staging = Settings(
        app_env="staging",
        daraja_mode="sandbox",
        callback_shared_secret="x" * 40,
        **LEGACY,
    )

    rejected = {issue.setting for issue in collect_config_issues(staging)}
    assert {"DARAJA_CONSUMER_KEY", "DARAJA_PASSKEY"} <= rejected
    with pytest.raises(ValueError, match="legacy unprefixed credentials"):
        RealDarajaClient(settings=staging)._required_credential("consumer_key")


def test_legacy_credentials_never_satisfy_production() -> None:
    production = Settings(app_env="production", daraja_mode="production", **LEGACY)

    rejected = {issue.setting for issue in collect_config_issues(production)}

    assert {"DARAJA_PRODUCTION_CONSUMER_KEY", "DARAJA_CONSUMER_KEY"} <= rejected
    with pytest.raises(ValueError, match="DARAJA_PRODUCTION_CONSUMER_KEY is required"):
        RealDarajaClient(settings=production)


def test_mode_specific_credentials_win_over_legacy() -> None:
    settings = Settings(
        app_env="development",
        daraja_mode="sandbox",
        daraja_sandbox_consumer_key="sandbox-key",
        **LEGACY,
    )

    resolved = resolve_credential(settings, "consumer_key", allow_legacy=True)

    assert resolved.value == "sandbox-key"
    assert resolved.setting == "DARAJA_SANDBOX_CONSUMER_KEY"
    assert RealDarajaClient(settings=settings)._required_credential("consumer_key") == "sandbox-key"


def test_production_credentials_are_never_selected_in_sandbox_mode() -> None:
    settings = Settings(
        app_env="staging",
        daraja_mode="sandbox",
        callback_shared_secret="x" * 40,
        daraja_sandbox_consumer_key="sandbox-key",
        daraja_production_consumer_key="production-key",
    )

    client = RealDarajaClient(settings=settings)

    assert client._required_credential("consumer_key") == "sandbox-key"
    assert client._base_url == SANDBOX
    assert "DARAJA_PRODUCTION_CONSUMER_KEY" in {
        issue.setting for issue in collect_config_issues(settings)
    }


# --- Normalization -----------------------------------------------------------------------


def test_mode_settings_are_canonicalized_for_every_consumer() -> None:
    settings = Settings(
        app_env="  Production ",
        daraja_mode=" Production ",
        storage_mode="POSTGRES",
        rate_limit_mode="Redis",
        callback_replay_mode=" REDIS",
        auth_mode="OIDC",
        oidc_provider_mode="JWKS",
        callback_source_verification_mode="Trusted_Proxy",
    )

    assert settings.app_env == "production"
    assert settings.daraja_mode == "production"
    assert settings.storage_mode == "postgres"
    assert settings.auth_mode == "oidc"
    assert settings.callback_source_verification_mode == "trusted_proxy"
    rejected = {issue.setting for issue in collect_config_issues(settings)}
    assert "DARAJA_PRODUCTION_CONSUMER_KEY" in rejected
    assert "STORAGE_MODE" not in rejected


def test_blank_app_env_is_treated_as_unset() -> None:
    assert Settings(app_env="   ").app_env is None


# --- Public callback destinations --------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "host"),
    [
        ("https://callbacks.mpesa-ops.co.ke/stk", "callbacks.mpesa-ops.co.ke"),
        ("https://CALLBACKS.mpesa-ops.co.ke/status/result", "callbacks.mpesa-ops.co.ke"),
        ("http://callbacks.mpesa-ops.co.ke/stk", None),
        ("https://10.0.0.5/stk", None),
        ("https://192.168.1.20/stk", None),
        ("https://127.0.0.1/stk", None),
        ("https://169.254.169.254/latest", None),
        ("https://[::1]/stk", None),
        ("https://[fe80::1]/stk", None),
        ("https://localhost/stk", None),
        ("https://callbacks.localhost/stk", None),
        ("https://payments.internal/stk", None),
        ("https://payments.corp/stk", None),
        ("https://callbacks.example/stk", None),
        ("https://callbacks.mpesa-ops.co.ke:8443/stk", None),
        ("https://user:pw@callbacks.mpesa-ops.co.ke/stk", None),
        ("https://callbacks.mpesa-ops.co.ke./stk", None),
        ("https://callbacks.mpesa-ops.co.ke/stk#frag", None),
        ("https://callbacks%2empesa-ops.co.ke/stk", None),
        (" https://callbacks.mpesa-ops.co.ke/stk", None),
        ("not a url", None),
    ],
)
def test_public_callback_host(url: str, host: str | None) -> None:
    assert public_callback_host(url) == host


# --- Numeric-host forms (shared hostname policy) -------------------------------------

NUMERIC_OR_ADDRESS_HOSTS = (
    "192.168.1.020",  # leading-zero octet
    "0300.0251.0326.0310",  # octal-like components
    "0177.0.0.1",  # octal loopback
    "1.2.3",  # short form
    "127.1",  # short-form loopback
    "0x7f000001",  # hexadecimal integer
    "0x7f.0x0.0x0.0x1",  # dotted hexadecimal
    "2130706433",  # decimal integer
    "127.0.0.1",  # standard IPv4 loopback
    "10.0.0.5",  # standard IPv4 private
    "169.254.169.254",  # standard IPv4 link-local
    "8.8.8.8",  # standard IPv4 public literal
    "[::1]",  # IPv6 loopback
    "[2001:db8::1]",  # IPv6 literal
    "[::ffff:127.0.0.1]",  # IPv4-mapped IPv6
    "callbacks.123",  # numeric-only final label
    "callbacks.0x1f",  # hex final label
    "10.0.0.1.nip.io",  # embedded numeric run
    "callbacks.1.2.mpesa-ops.co.ke",  # consecutive numeric components
    "127.0.0.1.",  # trailing dot
    "%31%32%37.0.0.1",  # percent-encoded digits
    "１９２.１６８.１.１",  # full-width digits
    "192.168.1.1。",  # ideographic full stop
    "1.2.3.4:443",  # address with port
)
VALID_DNS_HOSTS = (
    "callbacks.mpesa-ops.co.ke",
    "api.v2.mpesa-ops.co.ke",
    "123.mpesa-ops.co.ke",  # a single numeric label is valid DNS
    "1e100.net",  # digits plus letters are not an address
    "xn--80ak6aa92e.com",  # Punycode label
    "callbacks.xn--p1ai",  # Punycode top-level domain
)


@pytest.mark.parametrize("host", NUMERIC_OR_ADDRESS_HOSTS)
def test_numeric_and_address_hosts_are_rejected_by_both_url_policies(host: str) -> None:
    assert public_callback_host(f"https://{host}/mpesa/stk") is None
    assert canonical_https_origin(f"https://{host}") is None


@pytest.mark.parametrize("host", VALID_DNS_HOSTS)
def test_valid_dns_and_punycode_hosts_are_preserved(host: str) -> None:
    assert is_acceptable_dns_hostname(host)
    assert public_callback_host(f"https://{host}/mpesa/stk") == host
    assert canonical_https_origin(f"https://{host}") == f"https://{host}"


@pytest.mark.parametrize(
    "host",
    ["0300.0251.0326.0310", "127.1", "192.168.1.020", "0x7f000001"],
)
def test_numeric_sandbox_endpoint_never_gets_development_authority(host: str) -> None:
    settings = Settings(
        app_env="development",
        daraja_mode="sandbox",
        daraja_sandbox_base_url=f"https://{host}",
        operator_auth_enabled=False,
        operator_approver_token="static-approver-token",
    )

    assert classify_deployment(settings).tier is DeploymentTier.PRODUCTION
    assert allows_development_approval_authority(settings) is False
    with pytest.raises(ValueError, match="DARAJA_SANDBOX_BASE_URL is not a trusted"):
        RealDarajaClient(settings=settings)


def test_official_endpoints_still_resolve_after_hostname_hardening() -> None:
    assert canonical_https_origin(SANDBOX) == SANDBOX
    assert canonical_https_origin(PRODUCTION) == PRODUCTION
    assert is_acceptable_dns_hostname("sandbox.safaricom.co.ke")
    assert is_acceptable_dns_hostname("api.safaricom.co.ke")
