"""Daraja endpoint trust policy and credential resolution.

This module is the single interpretation of which Daraja endpoint and which
credentials a configuration selects. Deployment classification, startup validation,
and ``RealDarajaClient`` all call it, so the validator and the runtime cannot
disagree.

Endpoint trust is an allowlist, not a denylist:

- ``DARAJA_MODE=sandbox`` may only use ``https://sandbox.safaricom.co.ke``.
- ``DARAJA_MODE=production`` may only use ``https://api.safaricom.co.ke``.
- Custom gateways, reverse proxies, IP literals, ports, userinfo, non-https schemes,
  trailing-dot hostnames, percent-encoding, backslashes, paths, queries, and fragments
  are rejected. Local stubs must use ``DARAJA_MODE=mock``.

No production gateway policy exists. DNS is never consulted to establish trust.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.config import Settings

OFFICIAL_DARAJA_BASE_URLS: dict[str, str] = {
    "sandbox": "https://sandbox.safaricom.co.ke",
    "production": "https://api.safaricom.co.ke",
}
BASE_URL_SETTINGS: dict[str, str] = {
    "sandbox": "DARAJA_SANDBOX_BASE_URL",
    "production": "DARAJA_PRODUCTION_BASE_URL",
}
CREDENTIAL_NAMES = ("consumer_key", "consumer_secret", "passkey", "shortcode", "callback_url")

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOSTNAME = re.compile(rf"{_LABEL}(?:\.{_LABEL})+")
# A top-level label must start with a letter. No real TLD is numeric, and this rejects
# every form a resolver could parse as IPv4 (leading-zero, octal, hex, short-form, or
# integer addresses) because inet_aton-style parsing needs an all-numeric final part.
# Punycode TLDs (``xn--...``) start with a letter and remain valid.
_TOP_LEVEL_LABEL = re.compile(r"[a-z][a-z0-9-]*[a-z0-9]")
_NUMERIC_LABEL = re.compile(r"0x[0-9a-f]*|[0-9]+")
_MAX_HOSTNAME_LENGTH = 253
_AMBIGUOUS_CHARACTERS = ("\\", "%", "@", " ", "\t", "\r", "\n")


def is_acceptable_dns_hostname(host: str) -> bool:
    """Return whether ``host`` is an unambiguous DNS name, never an address.

    Shared by the Daraja endpoint policy and the callback destination policy. This is
    syntax only: it never performs DNS resolution and cannot prove who controls a name.

    Rejected: empty or over-long names, trailing dots, IP literals, any label outside
    lower-case letters/digits/hyphens, a top-level label that does not start with a
    letter (all numeric and hex address forms such as ``1.2.3``, ``127.1``,
    ``192.168.1.020``, ``0300.0251.0326.0310``, ``0x7f.0x1``), and two or more
    consecutive numeric or hex labels. The last rule is a defence-in-depth heuristic
    against names embedding an address (for example ``10.0.0.1.nip.io``); wildcard DNS
    that maps other names to private addresses remains an operational risk.
    """

    if host == "" or len(host) > _MAX_HOSTNAME_LENGTH or host.endswith("."):
        return False
    if _is_ip_literal(host) or _HOSTNAME.fullmatch(host) is None:
        return False
    labels = host.split(".")
    if _TOP_LEVEL_LABEL.fullmatch(labels[-1]) is None:
        return False
    consecutive_numeric = 0
    for label in labels:
        consecutive_numeric = consecutive_numeric + 1 if _NUMERIC_LABEL.fullmatch(label) else 0
        if consecutive_numeric >= 2:
            return False
    return True


def canonical_https_origin(url: str | None) -> str | None:
    """Return ``https://<host>`` for an unambiguous https origin, else ``None``.

    Only a bare https scheme and DNS hostname are accepted, optionally followed by a
    single ``/``. Hostnames are lower-cased. Anything that could be parsed differently
    by different URL parsers, or that names a non-default port, credentials, an IP
    literal, or a fully-qualified trailing-dot name, is rejected.
    """

    if not isinstance(url, str):
        return None
    candidate = url.strip()
    if candidate == "" or any(char in candidate for char in _AMBIGUOUS_CHARACTERS):
        return None
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return None
    if parts.scheme.lower() != "https" or parts.query or parts.fragment:
        return None
    if parts.path not in ("", "/"):
        return None
    host = parts.netloc.lower()
    if ":" in host or not is_acceptable_dns_hostname(host):
        return None
    return f"https://{host}"


NON_PUBLIC_HOST_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".lan",
    ".home.arpa",
    ".corp",
    ".test",
    ".example",
    ".invalid",
)


def public_callback_host(url: str | None) -> str | None:
    """Return the lower-cased host of an acceptable public https callback URL.

    Callback destinations receive transaction data from Safaricom, so they must be
    unambiguous https URLs on a public DNS name (``is_acceptable_dns_hostname``): no IP
    literal or numeric address form in any notation, no reserved or internal names, no
    explicit port, no userinfo, no fragment, and nothing parsers could read differently.
    Syntax cannot prove domain control; DNS ownership, TLS, and ingress are operational
    requirements.
    """

    if not isinstance(url, str):
        return None
    candidate = url.strip()
    if candidate == "" or candidate != url or any(
        char in candidate for char in _AMBIGUOUS_CHARACTERS
    ):
        return None
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return None
    if parts.scheme.lower() != "https" or parts.fragment:
        return None
    host = parts.netloc.lower()
    if ":" in host or not is_acceptable_dns_hostname(host):
        return None
    if host == "localhost" or host.endswith(NON_PUBLIC_HOST_SUFFIXES):
        return None
    return host


def configured_base_url(settings: Settings) -> str | None:
    """Return the configured base URL for the active real Daraja mode."""

    if settings.daraja_mode == "sandbox":
        return settings.daraja_sandbox_base_url
    if settings.daraja_mode == "production":
        return settings.daraja_production_base_url
    return None


def trusted_base_url(settings: Settings) -> str | None:
    """Return the official endpoint for the active mode if the configuration matches it."""

    official = OFFICIAL_DARAJA_BASE_URLS.get(settings.daraja_mode)
    if official is None:
        return None
    if canonical_https_origin(configured_base_url(settings)) != official:
        return None
    return official


def resolve_base_url(settings: Settings) -> str:
    """Return the trusted base URL for a real Daraja client or raise ``ValueError``."""

    resolved = trusted_base_url(settings)
    if resolved is None:
        setting = BASE_URL_SETTINGS.get(settings.daraja_mode, "DARAJA_MODE")
        raise ValueError(f"{setting} is not a trusted Daraja endpoint.")
    return resolved


@dataclass(frozen=True)
class ResolvedCredential:
    """Outcome of resolving one Daraja credential for the active mode."""

    name: str
    setting: str
    value: str | None
    legacy_present: bool

    @property
    def uses_legacy(self) -> bool:
        return self.value is not None and self.setting == legacy_setting_name(self.name)


def mode_setting_name(mode: str, name: str) -> str:
    return f"DARAJA_{mode.upper()}_{name.upper()}"


def legacy_setting_name(name: str) -> str:
    return f"DARAJA_{name.upper()}"


def resolve_credential(
    settings: Settings,
    name: str,
    *,
    allow_legacy: bool,
) -> ResolvedCredential:
    """Resolve a credential for the active mode.

    The mode-specific variable (``DARAJA_SANDBOX_*`` or ``DARAJA_PRODUCTION_*``) always
    wins. The legacy unprefixed ``DARAJA_*`` variable is used only when
    ``allow_legacy`` is true, which callers grant solely to contradiction-free local
    development configurations.
    """

    mode = settings.daraja_mode
    specific = _stripped(getattr(settings, f"daraja_{mode}_{name}", None))
    legacy = _stripped(getattr(settings, f"daraja_{name}", None))
    if specific is not None:
        return ResolvedCredential(name, mode_setting_name(mode, name), specific, legacy is not None)
    if legacy is not None and allow_legacy:
        return ResolvedCredential(name, legacy_setting_name(name), legacy, True)
    return ResolvedCredential(name, mode_setting_name(mode, name), None, legacy is not None)


def setting_value(settings: Settings, attribute: str) -> str | None:
    """Return a non-mode-specific Daraja setting as the runtime will send it.

    Used for transaction-status settings (initiator, security credential, result and
    timeout URLs) by both the validator and the client, so they agree on the value.
    """

    return _stripped(getattr(settings, attribute, None))


def legacy_credentials_permitted(settings: Settings) -> bool:
    """Return whether legacy unprefixed credentials may be used for this configuration."""

    from app.deployment import classify_deployment

    return classify_deployment(settings).allows_development_shortcuts


def _stripped(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _is_ip_literal(host: str) -> bool:
    candidate = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return True
