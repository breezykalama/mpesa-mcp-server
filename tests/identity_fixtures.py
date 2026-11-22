"""Operator identity builders for tests.

These mirror what server-side identity providers produce. Application code must never
build reviewer identities from request data; tests use these to exercise each
authentication provenance explicitly.
"""

from __future__ import annotations

from app.auth.oidc import OperatorIdentity, OperatorRole


def verified_human(
    subject: str = "verified-approver-1",
    *,
    roles: list[OperatorRole] | None = None,
    email: str | None = "approver@example.test",
    display_name: str = "Verified Approver",
) -> OperatorIdentity:
    """Return an IdP-verified delegated human identity (OIDC JWKS + ``scp``)."""

    return OperatorIdentity(
        subject=subject,
        email=email,
        display_name=display_name,
        roles=roles or ["viewer", "approver"],
        authentication_method="oidc_jwks",
        principal_type="human",
    )


def application_principal(
    subject: str = "service-principal-1",
    *,
    roles: list[OperatorRole] | None = None,
) -> OperatorIdentity:
    """Return an IdP-verified app-only (client credentials) identity."""

    return OperatorIdentity(
        subject=subject,
        display_name="Automation Service Principal",
        roles=roles or ["viewer", "approver", "admin"],
        authentication_method="oidc_jwks",
        principal_type="application",
    )


def static_token_identity(
    subject: str = "operator-approver",
    *,
    roles: list[OperatorRole] | None = None,
) -> OperatorIdentity:
    """Return a shared static bearer-token identity."""

    return OperatorIdentity(
        subject=subject,
        display_name="Operator Approver",
        roles=roles or ["approver"],
        authentication_method="static_token",
    )


def development_identity(
    subject: str = "development-operator",
    *,
    roles: list[OperatorRole] | None = None,
) -> OperatorIdentity:
    """Return a no-network development OIDC identity."""

    return OperatorIdentity(
        subject=subject,
        display_name="Development Operator",
        roles=roles or ["viewer", "approver"],
        authentication_method="oidc_development",
    )
