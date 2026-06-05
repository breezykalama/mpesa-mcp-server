"""Tests for operator identity and OIDC abstractions."""

from __future__ import annotations

from app.auth.oidc import DevelopmentOIDCIdentityProvider, OperatorIdentity
from app.auth.security import TokenIdentityProvider
from app.bootstrap.container import AppContainer
from app.config import Settings


def test_operator_identity_exposes_backwards_compatible_principal_fields() -> None:
    identity = OperatorIdentity(
        subject="operator-123",
        email="operator@example.test",
        display_name="Operator Example",
        roles=["viewer", "approver"],
    )

    assert identity.operator_id == "operator-123"
    assert identity.role == "approver"


def test_development_oidc_provider_returns_configured_identity() -> None:
    provider = DevelopmentOIDCIdentityProvider(
        Settings(
            operator_auth_enabled=False,
            oidc_development_subject="oidc-subject-123",
            oidc_development_email="oidc@example.test",
            oidc_development_display_name="OIDC Operator",
            oidc_development_groups="finance_viewers,finance_approvers",
            oidc_viewer_groups="finance_viewers",
            oidc_approver_groups="finance_approvers",
        )
    )

    identity = provider.authenticate("development-bearer-credential")

    assert identity is not None
    assert identity.subject == "oidc-subject-123"
    assert identity.email == "oidc@example.test"
    assert identity.groups == ["finance_viewers", "finance_approvers"]
    assert identity.roles == ["viewer", "approver"]
    assert identity.role == "approver"


def test_development_oidc_provider_rejects_missing_credential() -> None:
    provider = DevelopmentOIDCIdentityProvider(Settings(operator_auth_enabled=False))

    assert provider.authenticate(None) is None


def test_development_oidc_provider_rejects_unmapped_groups() -> None:
    provider = DevelopmentOIDCIdentityProvider(
        Settings(
            operator_auth_enabled=False,
            oidc_development_groups="unknown_group",
            oidc_viewer_groups="finance_viewers",
        )
    )

    assert provider.authenticate("development-bearer-credential") is None


def test_token_identity_provider_preserves_static_token_mapping() -> None:
    container = AppContainer.mock(
        settings=Settings(
            operator_auth_enabled=True,
            operator_viewer_token="viewer-token",
        )
    )
    provider = TokenIdentityProvider(container)

    identity = provider.authenticate("viewer-token")

    assert identity is not None
    assert identity.subject == "operator-viewer"
    assert identity.role == "viewer"
