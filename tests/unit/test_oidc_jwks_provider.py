"""Tests for standards-based OIDC JWT verification."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
from app.auth.oidc import JWKSOIDCIdentityProvider
from app.config import Settings
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.utils import base64url_encode

ISSUER = "https://identity.example.test"
AUDIENCE = "mpesa-operator-api"


def test_valid_jwt_verifies_successfully() -> None:
    key_pair = create_key_pair(kid="key-1")
    provider = build_provider(key_pair=key_pair)
    token = create_token(key_pair=key_pair, groups=["finance_approvers"])

    identity = provider.authenticate(token)

    assert identity is not None
    assert identity.subject == "operator-subject"
    assert identity.email == "operator@example.test"
    assert identity.display_name == "Operator Example"
    assert identity.groups == ["finance_approvers"]
    assert identity.roles == ["viewer", "approver"]


def test_roles_claim_is_mapped_to_platform_roles() -> None:
    key_pair = create_key_pair(kid="key-roles")
    provider = build_provider(key_pair=key_pair)
    token = create_token(key_pair=key_pair, roles=["finance_admins"])

    identity = provider.authenticate(token)

    assert identity is not None
    assert identity.groups == ["finance_admins"]
    assert identity.roles == ["viewer", "approver", "admin"]


def test_invalid_signature_rejected() -> None:
    jwks_key_pair = create_key_pair(kid="key-1")
    signing_key_pair = create_key_pair(kid="key-1")
    provider = build_provider(key_pair=jwks_key_pair)
    token = create_token(key_pair=signing_key_pair, groups=["finance_viewers"])

    assert provider.authenticate(token) is None


def test_expired_token_rejected() -> None:
    key_pair = create_key_pair(kid="key-expired")
    provider = build_provider(key_pair=key_pair)
    token = create_token(
        key_pair=key_pair,
        groups=["finance_viewers"],
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )

    assert provider.authenticate(token) is None


def test_wrong_issuer_rejected() -> None:
    key_pair = create_key_pair(kid="key-issuer")
    provider = build_provider(key_pair=key_pair)
    token = create_token(
        key_pair=key_pair,
        groups=["finance_viewers"],
        issuer="https://wrong-issuer.example.test",
    )

    assert provider.authenticate(token) is None


def test_wrong_audience_rejected() -> None:
    key_pair = create_key_pair(kid="key-audience")
    provider = build_provider(key_pair=key_pair)
    token = create_token(
        key_pair=key_pair,
        groups=["finance_viewers"],
        audience="wrong-audience",
    )

    assert provider.authenticate(token) is None


def test_unknown_kid_rejected() -> None:
    key_pair = create_key_pair(kid="known-key")
    provider = build_provider(key_pair=key_pair)
    token = create_token(
        key_pair=key_pair,
        groups=["finance_viewers"],
        token_kid="unknown-key",
    )

    assert provider.authenticate(token) is None


def test_discovery_fetch_failure_rejected() -> None:
    key_pair = create_key_pair(kid="key-discovery-failure")
    provider = build_provider(key_pair=key_pair, discovery_status=503)
    token = create_token(key_pair=key_pair, groups=["finance_viewers"])

    assert provider.authenticate(token) is None


def test_jwks_fetch_failure_rejected() -> None:
    key_pair = create_key_pair(kid="key-jwks-failure")
    provider = build_provider(key_pair=key_pair, jwks_status=503)
    token = create_token(key_pair=key_pair, groups=["finance_viewers"])

    assert provider.authenticate(token) is None


def test_token_without_mapped_groups_rejected() -> None:
    key_pair = create_key_pair(kid="key-no-groups")
    provider = build_provider(key_pair=key_pair)
    token = create_token(key_pair=key_pair, groups=["unknown_group"])

    assert provider.authenticate(token) is None


def build_provider(
    *,
    key_pair: dict[str, Any],
    discovery_status: int = 200,
    jwks_status: int = 200,
) -> JWKSOIDCIdentityProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(
                discovery_status,
                json={"jwks_uri": f"{ISSUER}/keys"},
            )
        if request.url.path == "/keys":
            return httpx.Response(
                jwks_status,
                json={"keys": [key_pair["jwk"]]},
            )
        return httpx.Response(404)

    settings = Settings(
        operator_auth_enabled=True,
        auth_mode="oidc",
        oidc_provider_mode="jwks",
        oidc_issuer=ISSUER,
        oidc_audience=AUDIENCE,
        oidc_viewer_groups="finance_viewers",
        oidc_approver_groups="finance_approvers",
        oidc_admin_groups="finance_admins",
    )
    return JWKSOIDCIdentityProvider(
        settings,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def create_key_pair(*, kid: str) -> dict[str, Any]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()
    return {
        "kid": kid,
        "private_key": private_key,
        "jwk": {
            "kty": "RSA",
            "kid": kid,
            "use": "sig",
            "alg": "RS256",
            "n": encode_number(public_numbers.n),
            "e": encode_number(public_numbers.e),
        },
    }


def create_token(
    *,
    key_pair: dict[str, Any],
    groups: list[str] | None = None,
    roles: list[str] | None = None,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    expires_at: datetime | None = None,
    token_kid: str | None = None,
) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "iss": issuer,
        "aud": audience,
        "sub": "operator-subject",
        "email": "operator@example.test",
        "name": "Operator Example",
        "iat": int(now.timestamp()),
        "nbf": int((now - timedelta(seconds=5)).timestamp()),
        "exp": int((expires_at or now + timedelta(minutes=5)).timestamp()),
    }
    if groups is not None:
        claims["groups"] = groups
    if roles is not None:
        claims["roles"] = roles

    return jwt.encode(
        claims,
        key_pair["private_key"],
        algorithm="RS256",
        headers={"kid": token_kid or key_pair["kid"]},
    )


def encode_number(number: int) -> str:
    return base64url_encode(number.to_bytes((number.bit_length() + 7) // 8, "big")).decode(
        "ascii"
    )
