"""Operator identity and OIDC provider abstractions."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Literal, Protocol

import httpx
import jwt
from pydantic import BaseModel, Field

from app.config import Settings

OperatorRole = Literal["viewer", "approver", "admin"]
ROLE_LEVELS: dict[OperatorRole, int] = {
    "viewer": 1,
    "approver": 2,
    "admin": 3,
}
SUPPORTED_JWT_ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"]
logger = logging.getLogger(__name__)


class OperatorIdentity(BaseModel):
    """Normalized operator identity returned by every authentication mode."""

    subject: str
    email: str | None = None
    display_name: str
    roles: list[OperatorRole] = Field(min_length=1)
    groups: list[str] = Field(default_factory=list)

    @property
    def operator_id(self) -> str:
        """Return the stable operator identifier used by existing workflows."""

        return self.subject

    @property
    def role(self) -> OperatorRole:
        """Return the highest role for backwards-compatible RBAC checks."""

        return max(self.roles, key=ROLE_LEVELS.__getitem__)


class OperatorIdentityProviderProtocol(Protocol):
    """Common interface for resolving credentials into operator identities."""

    def authenticate(self, credential: str | None) -> OperatorIdentity | None:
        """Authenticate a credential and return a normalized identity."""


class OIDCIdentityProviderProtocol(OperatorIdentityProviderProtocol, Protocol):
    """Marker interface for OIDC-backed operator identity providers."""


class DevelopmentOIDCIdentityProvider:
    """No-network OIDC placeholder for development and tests only."""

    def __init__(self, settings: Settings) -> None:
        from app.auth.role_mapping import OIDCRoleMapper

        groups = _parse_csv(settings.oidc_development_groups)
        mapped_roles = OIDCRoleMapper.from_settings(settings).map_groups(groups).roles
        self._identity: OperatorIdentity | None = None
        if mapped_roles:
            self._identity = OperatorIdentity(
                subject=settings.oidc_development_subject,
                email=settings.oidc_development_email,
                display_name=settings.oidc_development_display_name,
                roles=mapped_roles,
                groups=groups,
            )

    def authenticate(self, credential: str | None) -> OperatorIdentity | None:
        """Return configured development identity for a non-empty bearer credential."""

        if credential is None or credential.strip() == "" or self._identity is None:
            return None
        return self._identity.model_copy(deep=True)


def _parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class JWKSOIDCIdentityProvider:
    """Verify OIDC bearer JWTs using discovery and JWKS documents."""

    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.Client | None = None,
        time_provider: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._http_client = http_client or httpx.Client(
            timeout=settings.daraja_request_timeout_seconds
        )
        self._time_provider = time_provider
        self._discovery_cache: dict[str, object] | None = None
        self._jwks_cache: dict[str, object] | None = None
        self._discovery_expires_at = 0.0
        self._jwks_expires_at = 0.0

    def authenticate(self, credential: str | None) -> OperatorIdentity | None:
        """Verify a bearer JWT and return a normalized operator identity."""

        if credential is None or credential.strip() == "":
            return None

        try:
            claims = self._verify_token(credential)
            groups = self._extract_groups(claims)
            from app.auth.role_mapping import OIDCRoleMapper

            mapped_roles = OIDCRoleMapper.from_settings(self._settings).map_groups(
                groups
            ).roles
            if not mapped_roles:
                return None

            subject = _claim_as_string(claims.get("sub"))
            if subject is None:
                return None

            email = _claim_as_string(claims.get(self._settings.oidc_email_claim))
            display_name = (
                _claim_as_string(claims.get(self._settings.oidc_name_claim))
                or _claim_as_string(claims.get("preferred_username"))
                or email
                or subject
            )
            return OperatorIdentity(
                subject=subject,
                email=email,
                display_name=display_name,
                roles=mapped_roles,
                groups=groups,
            )
        except Exception:
            logger.warning(
                "OIDC token verification failed.",
                extra={"event_type": "oidc_token_verification_failed"},
                exc_info=True,
            )
            return None

    def _verify_token(self, token: str) -> dict[str, object]:
        header = jwt.get_unverified_header(token)
        key_id = header.get("kid")
        if not isinstance(key_id, str) or key_id == "":
            raise ValueError("OIDC token is missing a key id.")

        jwk = self._find_jwk(key_id)
        algorithm = jwk.get("alg")
        if not isinstance(algorithm, str) or algorithm not in SUPPORTED_JWT_ALGORITHMS:
            algorithm = _claim_as_string(header.get("alg")) or "RS256"
        if algorithm not in SUPPORTED_JWT_ALGORITHMS:
            raise ValueError("OIDC token uses an unsupported signing algorithm.")

        signing_key = jwt.PyJWK.from_dict(jwk, algorithm=algorithm).key
        issuer = _required_setting(self._settings.oidc_issuer, "OIDC_ISSUER")
        audience = _required_setting(self._settings.oidc_audience, "OIDC_AUDIENCE")
        claims = jwt.decode(
            token,
            key=signing_key,
            algorithms=[algorithm],
            audience=audience,
            issuer=issuer.rstrip("/"),
            leeway=self._settings.oidc_clock_skew_seconds,
            options={"require": ["exp", "sub"]},
        )
        if not isinstance(claims, dict):
            raise ValueError("OIDC token claims must be a JSON object.")
        return claims

    def _find_jwk(self, key_id: str) -> dict[str, object]:
        jwks = self._get_jwks()
        keys = jwks.get("keys")
        if not isinstance(keys, list):
            raise ValueError("JWKS document is missing keys.")

        for key in keys:
            if isinstance(key, dict) and key.get("kid") == key_id:
                return key

        raise ValueError("OIDC token key id was not found in JWKS.")

    def _get_discovery_document(self) -> dict[str, object]:
        now = self._time_provider()
        if self._discovery_cache is not None and now < self._discovery_expires_at:
            return self._discovery_cache

        issuer = _required_setting(self._settings.oidc_issuer, "OIDC_ISSUER")
        url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
        response = self._http_client.get(url)
        response.raise_for_status()
        document = response.json()
        if not isinstance(document, dict):
            raise ValueError("OIDC discovery document must be a JSON object.")

        self._discovery_cache = document
        self._discovery_expires_at = now + self._settings.oidc_jwks_cache_seconds
        return document

    def _get_jwks(self) -> dict[str, object]:
        now = self._time_provider()
        if self._jwks_cache is not None and now < self._jwks_expires_at:
            return self._jwks_cache

        discovery = self._get_discovery_document()
        jwks_uri = discovery.get("jwks_uri")
        if not isinstance(jwks_uri, str) or jwks_uri.strip() == "":
            raise ValueError("OIDC discovery document is missing jwks_uri.")

        response = self._http_client.get(jwks_uri)
        response.raise_for_status()
        jwks = response.json()
        if not isinstance(jwks, dict):
            raise ValueError("JWKS document must be a JSON object.")

        self._jwks_cache = jwks
        self._jwks_expires_at = now + self._settings.oidc_jwks_cache_seconds
        return jwks

    def _extract_groups(self, claims: dict[str, object]) -> list[str]:
        groups = _claim_as_list(claims.get(self._settings.oidc_groups_claim))
        roles = _claim_as_list(claims.get(self._settings.oidc_roles_claim))
        return list(dict.fromkeys([*groups, *roles]))


def _claim_as_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item.strip()]
    return []


def _claim_as_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip() != "":
        return value
    return None


def _required_setting(value: str | None, name: str) -> str:
    if value is None or value.strip() == "":
        raise ValueError(f"{name} is required.")
    return value
