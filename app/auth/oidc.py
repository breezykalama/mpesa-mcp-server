"""Operator identity and OIDC provider abstractions."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field

from app.config import Settings

OperatorRole = Literal["viewer", "approver", "admin"]
ROLE_LEVELS: dict[OperatorRole, int] = {
    "viewer": 1,
    "approver": 2,
    "admin": 3,
}


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
