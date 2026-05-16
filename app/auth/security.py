"""Lightweight operator authentication and RBAC dependencies."""

from __future__ import annotations

import logging
from secrets import compare_digest
from typing import Annotated, Literal

from fastapi import Depends, Header, HTTPException, status
from pydantic import BaseModel

from app.bootstrap.container import AppContainer
from app.callbacks.routes import get_app_container

OperatorRole = Literal["viewer", "approver", "admin"]
ROLE_LEVELS: dict[OperatorRole, int] = {
    "viewer": 1,
    "approver": 2,
    "admin": 3,
}

logger = logging.getLogger(__name__)


class OperatorPrincipal(BaseModel):
    """Authenticated operator principal."""

    operator_id: str
    role: OperatorRole


def get_operator_principal(
    container: Annotated[AppContainer, Depends(get_app_container)],
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> OperatorPrincipal:
    """Authenticate an operator from a bearer token."""

    if not container.settings.operator_auth_enabled:
        principal = OperatorPrincipal(operator_id="local-admin", role="admin")
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

    token_principal = _principal_for_token(container, token)
    if token_principal is None:
        logger.warning(
            "Operator authentication failed.",
            extra={"event_type": "operator_auth_failed", "reason": "invalid_token"},
        )
        raise _unauthorized()

    logger.info(
        "Operator authenticated.",
        extra={
            "event_type": "operator_auth_success",
            "operator_id": token_principal.operator_id,
            "role": token_principal.role,
        },
    )
    return token_principal


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


def _principal_for_token(
    container: AppContainer,
    token: str,
) -> OperatorPrincipal | None:
    token_map: tuple[tuple[str | None, OperatorPrincipal], ...] = (
        (
            container.settings.operator_viewer_token,
            OperatorPrincipal(operator_id="operator-viewer", role="viewer"),
        ),
        (
            container.settings.operator_approver_token,
            OperatorPrincipal(operator_id="operator-approver", role="approver"),
        ),
        (
            container.settings.operator_admin_token,
            OperatorPrincipal(operator_id="operator-admin", role="admin"),
        ),
    )
    for configured_token, principal in token_map:
        if configured_token and compare_digest(token, configured_token):
            return principal

    return None


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
