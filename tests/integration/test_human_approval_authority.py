"""End-to-end tests for human approval authority through the operator HTTP API.

Tokens are real RS256 JWTs verified by ``JWKSOIDCIdentityProvider`` against an
in-process mock discovery/JWKS endpoint. No network or provider calls leave the test.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from app.auth import security
from app.auth.oidc import JWKSOIDCIdentityProvider
from app.bootstrap.container import AppContainer
from app.callbacks.routes import get_app_container
from app.config import Settings
from app.main import app
from fastapi.testclient import TestClient
from tests.unit.test_oidc_jwks_provider import (
    AUDIENCE,
    ISSUER,
    create_key_pair,
    create_token,
)

TEST_DATABASE_URL = "postgresql+asyncpg://mpesa:mpesa@localhost:5432/mpesa_mcp"
SIGNING_KEY = create_key_pair(kid="approval-authority-key")
DELEGATED: dict[str, object] = {"scp": "operator.access"}


@pytest.fixture(autouse=True)
def mock_jwks_endpoint(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={"jwks_uri": f"{ISSUER}/keys"})
        if request.url.path == "/keys":
            return httpx.Response(200, json={"keys": [SIGNING_KEY["jwk"]]})
        return httpx.Response(404)

    def provider_factory(settings: Settings) -> JWKSOIDCIdentityProvider:
        return JWKSOIDCIdentityProvider(
            settings,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

    monkeypatch.setattr(security, "JWKSOIDCIdentityProvider", provider_factory)
    yield
    app.dependency_overrides.clear()


def build_container(**overrides: Any) -> AppContainer:
    settings: dict[str, Any] = {
        "database_url": TEST_DATABASE_URL,
        "app_env": "staging",
        "auth_mode": "oidc",
        "oidc_provider_mode": "jwks",
        "oidc_issuer": ISSUER,
        "oidc_audience": AUDIENCE,
        "oidc_viewer_groups": "finance_viewers",
        "oidc_approver_groups": "finance_approvers",
        "oidc_admin_groups": "finance_admins",
    }
    settings.update(overrides)
    return AppContainer.mock(settings=Settings(**settings))


def token_for(
    subject: str,
    *,
    groups: list[str] | None = None,
    extra_claims: dict[str, object] | None = DELEGATED,
    signing_key: dict[str, Any] = SIGNING_KEY,
) -> str:
    claims: dict[str, object] = {
        "sub": subject,
        "email": f"{subject}@example.test",
        "name": subject.replace("-", " ").title(),
    }
    claims.update(extra_claims or {})
    return create_token(
        key_pair=signing_key,
        groups=groups or ["finance_approvers"],
        extra_claims=claims,
    )


def create_agent_request(container: AppContainer, *, amount: int | None = None) -> str:
    response = container.payment_service.initiate_stk_push(
        phone_number="254700000000",
        amount=amount or container.settings.max_stk_amount + 1,
        account_reference=f"INV-HUMAN-{len(container.audit_logger.events)}",
        description="Agent requested payment",
        request_origin="mcp_agent",
    )
    assert response.approval_id is not None
    return response.approval_id


def post(
    container: AppContainer,
    path: str,
    *,
    token: str | None = None,
    json: dict[str, Any] | None = None,
) -> httpx.Response:
    app.dependency_overrides[get_app_container] = lambda: container
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return TestClient(app).post(path, headers=headers, json=json)


def approval_status(container: AppContainer, approval_id: str) -> str:
    approval = container.approval_service.get_approval_request(approval_id)
    assert approval is not None
    return approval.status


@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize(
    "authorization",
    [None, "not-a-jwt", "signed-by-unknown-key"],
    ids=["missing", "malformed", "bad-signature"],
)
def test_missing_or_invalid_authentication_fails_closed(
    action: str,
    authorization: str | None,
) -> None:
    container = build_container()
    approval_id = create_agent_request(container)
    token = authorization
    if authorization == "signed-by-unknown-key":
        token = token_for(
            "attacker",
            signing_key=create_key_pair(kid="approval-authority-key"),
        )

    response = post(container, f"/approvals/{approval_id}/{action}", token=token)

    assert response.status_code == 401
    assert approval_status(container, approval_id) == "pending"
    assert container.transaction_repository.list_transactions() == []


def test_verified_human_approver_can_approve_and_identity_is_audited() -> None:
    container = build_container()
    approval_id = create_agent_request(container)

    response = post(
        container,
        f"/approvals/{approval_id}/approve",
        token=token_for("alice-approver"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert body["payment"]["status"] == "pending"
    assert body["approval"]["reviewer_ids"] == ["alice-approver"]
    assert len(container.transaction_repository.list_transactions()) == 1
    [event] = [e for e in container.audit_logger.events if e.event_type == "approval_approved"]
    assert event.operator_subject == "alice-approver"
    assert event.operator_email == "alice-approver@example.test"
    assert event.operator_display_name == "Alice Approver"
    assert event.operator_roles == ["viewer", "approver"]
    assert event.payload["reviewer_assurance"] == "verified_human"


def test_verified_human_approver_can_reject_and_identity_is_audited() -> None:
    container = build_container()
    approval_id = create_agent_request(container)

    response = post(
        container,
        f"/approvals/{approval_id}/reject",
        token=token_for("bob-approver"),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    [event] = [e for e in container.audit_logger.events if e.event_type == "approval_rejected"]
    assert event.operator_subject == "bob-approver"
    assert event.actor == "authenticated_operator"


def test_request_body_identity_fields_are_ignored() -> None:
    container = build_container()
    approval_id = create_agent_request(container)

    response = post(
        container,
        f"/approvals/{approval_id}/approve",
        token=token_for("carol-approver"),
        json={
            "operator_id": "spoofed-operator",
            "subject": "spoofed-subject",
            "reviewer": {"subject": "spoofed", "authentication_method": "oidc_jwks"},
        },
    )

    assert response.status_code == 200
    assert response.json()["approval"]["reviewer_ids"] == ["carol-approver"]
    serialized_audit = "".join(e.model_dump_json() for e in container.audit_logger.events)
    assert "spoofed" not in serialized_audit


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_app_only_token_cannot_decide(action: str) -> None:
    container = build_container()
    approval_id = create_agent_request(container)

    response = post(
        container,
        f"/approvals/{approval_id}/{action}",
        token=token_for("automation-sp", extra_claims={"idtyp": "app"}),
    )

    assert response.status_code == 403
    assert response.json()["status"] == "reviewer_not_authorized"
    assert approval_status(container, approval_id) == "pending"
    assert container.transaction_repository.list_transactions() == []


def test_token_without_delegated_user_evidence_cannot_approve() -> None:
    container = build_container()
    approval_id = create_agent_request(container)

    response = post(
        container,
        f"/approvals/{approval_id}/approve",
        token=token_for("unknown-principal", extra_claims=None),
    )

    assert response.status_code == 403
    assert container.transaction_repository.list_transactions() == []


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_verified_viewer_cannot_decide(action: str) -> None:
    container = build_container()
    approval_id = create_agent_request(container)

    response = post(
        container,
        f"/approvals/{approval_id}/{action}",
        token=token_for("victor-viewer", groups=["finance_viewers"]),
    )

    assert response.status_code == 403
    assert approval_status(container, approval_id) == "pending"


def test_non_pending_approval_cannot_be_rejected_over_http() -> None:
    container = build_container()
    approval_id = create_agent_request(container)
    post(container, f"/approvals/{approval_id}/approve", token=token_for("alice-approver"))

    response = post(
        container,
        f"/approvals/{approval_id}/reject",
        token=token_for("bob-approver"),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "blocked"
    assert approval_status(container, approval_id) == "approved"


def test_repeated_http_approvals_create_one_payment() -> None:
    container = build_container()
    approval_id = create_agent_request(container)

    statuses = [
        post(
            container,
            f"/approvals/{approval_id}/approve",
            token=token_for(subject),
        ).json()["status"]
        for subject in ("alice-approver", "alice-approver", "bob-approver")
    ]

    assert statuses == ["approved", "blocked", "blocked"]
    assert len(container.transaction_repository.list_transactions()) == 1


def test_high_risk_approval_requires_two_distinct_verified_humans() -> None:
    container = build_container()
    approval_id = create_agent_request(
        container,
        amount=container.settings.high_risk_amount_threshold,
    )

    first = post(container, f"/approvals/{approval_id}/approve", token=token_for("alice-approver"))
    second = post(container, f"/approvals/{approval_id}/approve", token=token_for("bob-approver"))

    assert first.json()["status"] == "partially_approved"
    assert second.json()["status"] == "approved"
    assert len(container.transaction_repository.list_transactions()) == 1


def test_static_token_cannot_approve_outside_development() -> None:
    container = build_container(auth_mode="token", operator_approver_token="approver-token")
    approval_id = create_agent_request(container)

    response = post(container, f"/approvals/{approval_id}/approve", token="approver-token")

    assert response.status_code == 403
    assert response.json()["status"] == "reviewer_not_authorized"
    assert container.transaction_repository.list_transactions() == []


def test_development_oidc_identity_cannot_approve_outside_development() -> None:
    container = build_container(
        oidc_provider_mode="development",
        oidc_development_groups="finance_approvers",
    )
    approval_id = create_agent_request(container)

    response = post(container, f"/approvals/{approval_id}/approve", token="any-credential")

    assert response.status_code == 403
    assert container.transaction_repository.list_transactions() == []


def test_auth_disabled_principal_cannot_approve_outside_development() -> None:
    container = build_container(auth_mode="token", operator_auth_enabled=False)
    approval_id = create_agent_request(container)

    response = post(container, f"/approvals/{approval_id}/approve")

    assert response.status_code == 403
    assert container.transaction_repository.list_transactions() == []


# --- Authorization ordering (F2) --------------------------------------------------


def request_in_state(container: AppContainer, state: str) -> str:
    approval_id = create_agent_request(container)
    if state == "approved":
        post(container, f"/approvals/{approval_id}/approve", token=token_for("alice-approver"))
    elif state == "rejected":
        post(container, f"/approvals/{approval_id}/reject", token=token_for("alice-approver"))
    elif state == "expired":
        container.approval_service.expire_stale_approvals()
    return approval_id


@pytest.mark.parametrize(
    "state",
    ["pending", "expired_unmutated", "approved", "rejected", "expired"],
)
@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize(
    "ineligible_token",
    ["app-only", "no-delegation"],
)
def test_ineligible_token_gets_403_without_state_change(
    ineligible_token: str,
    action: str,
    state: str,
) -> None:
    container = build_container(
        approval_expiry_minutes=0 if state.startswith("expired") else 30,
    )
    approval_id = request_in_state(container, state)
    stored_before = container.approval_service.get_approval_request(approval_id)
    transactions_before = len(container.transaction_repository.list_transactions())
    token = token_for(
        "ineligible-principal",
        extra_claims={"idtyp": "app"} if ineligible_token == "app-only" else None,
    )

    response = post(container, f"/approvals/{approval_id}/{action}", token=token)

    assert response.status_code == 403
    assert response.json()["status"] == "reviewer_not_authorized"
    assert response.json()["approval"] is None
    assert container.approval_service.get_approval_request(approval_id) == stored_before
    assert len(container.transaction_repository.list_transactions()) == transactions_before
    denied = [
        e for e in container.audit_logger.events if e.event_type == "approval_decision_denied"
    ]
    assert len(denied) == 1
    assert denied[0].operator_subject == "ineligible-principal"


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_ineligible_token_on_unknown_approval_gets_403_not_404(action: str) -> None:
    container = build_container()

    response = post(
        container,
        f"/approvals/does-not-exist/{action}",
        token=token_for("automation-sp", extra_claims={"idtyp": "app"}),
    )

    assert response.status_code == 403
    assert response.json()["status"] == "reviewer_not_authorized"
