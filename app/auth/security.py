"""Lightweight operator authentication and RBAC dependencies."""

from __future__ import annotations

import logging
from secrets import compare_digest
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.auth.oidc import (
    ROLE_LEVELS,
    DevelopmentOIDCIdentityProvider,
    JWKSOIDCIdentityProvider,
    OperatorIdentity,
    OperatorIdentityProviderProtocol,
    OperatorRole,
)
from app.bootstrap.container import AppContainer
from app.callbacks.routes import get_app_container

logger = logging.getLogger(__name__)

OperatorPrincipal = OperatorIdentity


def get_operator_principal(
    container: Annotated[AppContainer, Depends(get_app_container)],
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> OperatorPrincipal:
    """Authenticate an operator from a bearer token."""

    if not container.settings.operator_auth_enabled:
        principal = OperatorIdentity(
            subject="local-admin",
            display_name="Local Administrator",
            roles=["admin"],
        )
        logger.info(
            "Operator auth disabled; local principal granted.",
            extra={
                "event_type": "operator_auth_success",
                "operator_id": principal.operator_id,
                "role": principal.role,
            },
        )
        return principal

    token = _extract_bearer_token(authorization)
    if token is None:
        logger.warning(
            "Operator authentication failed.",
            extra={"event_type": "operator_auth_failed", "reason": "missing_token"},
        )
        raise _unauthorized()

    identity = _identity_provider(container).authenticate(token)
    if identity is None:
        logger.warning(
            "Operator authentication failed.",
            extra={"event_type": "operator_auth_failed", "reason": "invalid_token"},
        )
        raise _unauthorized()

    logger.info(
        "Operator authenticated.",
        extra={
            "event_type": "operator_auth_success",
            "operator_id": identity.operator_id,
            "role": identity.role,
            "auth_mode": container.settings.auth_mode,
        },
    )
    return identity


def require_viewer(
    principal: Annotated[OperatorPrincipal, Depends(get_operator_principal)],
) -> OperatorPrincipal:
    """Require viewer-or-higher access."""

    return _require_role(principal, "viewer")


def require_approver(
    principal: Annotated[OperatorPrincipal, Depends(get_operator_principal)],
) -> OperatorPrincipal:
    """Require approver-or-higher access."""

    return _require_role(principal, "approver")


def require_admin(
    principal: Annotated[OperatorPrincipal, Depends(get_operator_principal)],
) -> OperatorPrincipal:
    """Require admin access."""

    return _require_role(principal, "admin")


def _extract_bearer_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None

    scheme, separator, token = authorization.partition(" ")
    if separator == "" or scheme.lower() != "bearer" or token == "":
        return None

    return token


class TokenIdentityProvider:
    """Resolve configured static operator tokens into normalized identities."""

    def __init__(self, container: AppContainer) -> None:
        self._container = container

    def authenticate(self, credential: str | None) -> OperatorIdentity | None:
        """Authenticate using the existing static bearer token mapping."""

        if credential is None:
            return None

        token_map: tuple[tuple[str | None, OperatorIdentity], ...] = (
            (
                self._container.settings.operator_viewer_token,
                OperatorIdentity(
                    subject="operator-viewer",
                    display_name="Operator Viewer",
                    roles=["viewer"],
                ),
            ),
            (
                self._container.settings.operator_approver_token,
                OperatorIdentity(
                    subject="operator-approver",
                    display_name="Operator Approver",
                    roles=["approver"],
                ),
            ),
            (
                self._container.settings.operator_admin_token,
                OperatorIdentity(
                    subject="operator-admin",
                    display_name="Operator Administrator",
                    roles=["admin"],
                ),
            ),
        )
        for configured_token, identity in token_map:
            if configured_token and compare_digest(credential, configured_token):
                return identity

        return None


def _identity_provider(container: AppContainer) -> OperatorIdentityProviderProtocol:
    if container.settings.auth_mode == "token":
        return TokenIdentityProvider(container)
    if container.settings.auth_mode == "oidc":
        if container.settings.oidc_provider_mode == "development":
            return DevelopmentOIDCIdentityProvider(container.settings)
        if container.settings.oidc_provider_mode == "jwks":
            return JWKSOIDCIdentityProvider(container.settings)
    raise ValueError("AUTH_MODE must be one of: token, oidc.")


def _require_role(
    principal: OperatorPrincipal,
    minimum_role: OperatorRole,
) -> OperatorPrincipal:
    if ROLE_LEVELS[principal.role] >= ROLE_LEVELS[minimum_role]:
        return principal

    logger.warning(
        "Operator authorization denied.",
        extra={
            "event_type": "operator_authorization_denied",
            "operator_id": principal.operator_id,
            "role": principal.role,
            "required_role": minimum_role,
        },
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Operator is not authorized for this action.",
    )


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid operator credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )
