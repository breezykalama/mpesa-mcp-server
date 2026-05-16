"""Integration tests for operator authentication and RBAC."""

from __future__ import annotations

import logging

import pytest
from app.bootstrap.container import AppContainer
from app.callbacks.routes import get_app_container
from app.config import Settings
from app.main import app
from fastapi.testclient import TestClient
from httpx import Response

VIEWER_TOKEN = "viewer-token"
APPROVER_TOKEN = "approver-token"
ADMIN_TOKEN = "admin-token"


def test_missing_token_rejected() -> None:
    container = build_auth_container()

    response = request_with_container(container, "GET", "/operator/transactions")

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid operator credentials."


def test_invalid_token_rejected() -> None:
    container = build_auth_container()

    response = request_with_container(
        container,
        "GET",
        "/operator/transactions",
        token="wrong-token",
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid operator credentials."


def test_viewer_can_read_operator_endpoints() -> None:
    container = build_auth_container()
    seed_transaction(container)

    response = request_with_container(
        container,
        "GET",
        "/operator/transactions",
        token=VIEWER_TOKEN,
    )

    assert response.status_code == 200
    assert len(response.json()["transactions"]) == 1


def test_viewer_cannot_approve() -> None:
    container = build_auth_container()
    approval_id = create_pending_approval(container)

    response = request_with_container(
        container,
        "POST",
        f"/approvals/{approval_id}/approve",
        token=VIEWER_TOKEN,
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Operator is not authorized for this action."
    assert container.transaction_repository.list_transactions() == []


def test_approver_can_approve_and_reject() -> None:
    container = build_auth_container()
    approval_to_approve = create_pending_approval(container)
    approval_to_reject = create_pending_approval(container)

    approve_response = request_with_container(
        container,
        "POST",
        f"/approvals/{approval_to_approve}/approve",
        token=APPROVER_TOKEN,
    )
    reject_response = request_with_container(
        container,
        "POST",
        f"/approvals/{approval_to_reject}/reject",
        token=APPROVER_TOKEN,
    )

    assert approve_response.status_code == 200
    assert approve_response.json()["status"] == "approved"
    assert reject_response.status_code == 200
    assert reject_response.json()["status"] == "rejected"


def test_approver_cannot_run_admin_reconciliation() -> None:
    container = build_auth_container()
    seed_transaction(container)

    response = request_with_container(
        container,
        "POST",
        "/operator/reconciliation/run",
        token=APPROVER_TOKEN,
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Operator is not authorized for this action."


def test_admin_can_do_everything() -> None:
    container = build_auth_container()
    seed_transaction(container)
    approval_id = create_pending_approval(container)

    transactions_response = request_with_container(
        container,
        "GET",
        "/operator/transactions",
        token=ADMIN_TOKEN,
    )
    reconciliation_response = request_with_container(
        container,
        "POST",
        "/operator/reconciliation/run",
        token=ADMIN_TOKEN,
    )
    approval_response = request_with_container(
        container,
        "POST",
        f"/approvals/{approval_id}/approve",
        token=ADMIN_TOKEN,
    )

    assert transactions_response.status_code == 200
    assert reconciliation_response.status_code == 200
    assert reconciliation_response.json()["summary"]["status"] == "ok"
    assert approval_response.status_code == 200
    assert approval_response.json()["status"] == "approved"


def test_auth_disabled_allows_local_access() -> None:
    container = build_auth_container(operator_auth_enabled=False)

    response = request_with_container(container, "GET", "/operator/transactions")

    assert response.status_code == 200
    assert response.json()["transactions"] == []


def test_auth_does_not_expose_tokens(
    caplog: pytest.LogCaptureFixture,
) -> None:
    container = build_auth_container()

    with caplog.at_level(logging.INFO):
        response = request_with_container(
            container,
            "GET",
            "/operator/transactions",
            token=VIEWER_TOKEN,
        )

    combined_text = response.text + caplog.text
    assert response.status_code == 200
    assert VIEWER_TOKEN not in combined_text
    assert APPROVER_TOKEN not in combined_text
    assert ADMIN_TOKEN not in combined_text


def build_auth_container(*, operator_auth_enabled: bool = True) -> AppContainer:
    return AppContainer.mock(
        settings=Settings(
            database_url="postgresql+asyncpg://mpesa:mpesa@localhost:5432/mpesa_mcp",
            operator_auth_enabled=operator_auth_enabled,
            operator_viewer_token=VIEWER_TOKEN,
            operator_approver_token=APPROVER_TOKEN,
            operator_admin_token=ADMIN_TOKEN,
        )
    )


def seed_transaction(container: AppContainer) -> str:
    transaction = container.transaction_repository.save_pending_transaction(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-AUTH-001",
        description="Auth route test payment",
        checkout_request_id="ws_CO_AUTH",
        merchant_request_id="mock_auth",
        idempotency_key="auth-route-001",
    )
    return transaction.transaction_id


def create_pending_approval(container: AppContainer) -> str:
    response = container.payment_service.initiate_stk_push(
        phone_number="254700000000",
        amount=10_001,
        account_reference="INV-AUTH-APPROVAL-001",
        description="Auth approval route test payment",
        idempotency_key=f"auth-approval-{len(container.audit_logger.events)}",
    )

    assert response.approval_id is not None
    return response.approval_id


def request_with_container(
    container: AppContainer,
    method: str,
    path: str,
    *,
    token: str | None = None,
) -> Response:
    headers = {"Authorization": f"Bearer {token}"} if token is not None else None
    app.dependency_overrides[get_app_container] = lambda: container
    try:
        client = TestClient(app)
        return client.request(method, path, headers=headers)
    finally:
        app.dependency_overrides.clear()
