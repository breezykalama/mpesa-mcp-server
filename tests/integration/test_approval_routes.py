"""Integration tests for operator approval routes."""

from __future__ import annotations

from app.bootstrap.container import AppContainer
from app.callbacks.routes import get_app_container
from app.config import Settings
from app.main import app
from fastapi.testclient import TestClient
from httpx import Response


def test_pending_approvals_list() -> None:
    container = build_approval_container()
    pending_approval_id = create_pending_approval(container)
    rejected_approval_id = create_pending_approval(container)
    container.approval_service.reject_request(rejected_approval_id)

    response = request_with_container(container, "GET", "/approvals/pending")

    assert response.status_code == 200
    body = response.json()
    assert [approval["approval_id"] for approval in body["approvals"]] == [
        pending_approval_id
    ]


def test_get_approval_by_id() -> None:
    container = build_approval_container()
    approval_id = create_pending_approval(container)

    response = request_with_container(container, "GET", f"/approvals/{approval_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["approval_id"] == approval_id
    assert body["status"] == "pending"
    assert body["payload"]["amount"] == 10_001


def test_approve_executes_payment() -> None:
    container = build_approval_container()
    approval_id = create_pending_approval(container)

    response = request_with_container(
        container,
        "POST",
        f"/approvals/{approval_id}/approve",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert body["approval"]["approval_id"] == approval_id
    assert body["approval"]["status"] == "approved"
    assert body["payment"]["status"] == "pending"
    assert body["payment"]["checkout_request_id"].startswith("ws_CO_")
    assert len(container.transaction_repository.list_transactions()) == 1


def test_reject_blocks_execution() -> None:
    container = build_approval_container()
    approval_id = create_pending_approval(container)

    reject_response = request_with_container(
        container,
        "POST",
        f"/approvals/{approval_id}/reject",
    )
    approve_response = request_with_container(
        container,
        "POST",
        f"/approvals/{approval_id}/approve",
    )

    assert reject_response.status_code == 200
    assert reject_response.json()["status"] == "rejected"
    assert approve_response.status_code == 200
    assert approve_response.json()["status"] == "blocked"
    assert approve_response.json()["approval"]["status"] == "rejected"
    assert container.transaction_repository.list_transactions() == []


def test_missing_approval_returns_404() -> None:
    container = build_approval_container()

    get_response = request_with_container(container, "GET", "/approvals/missing")
    approve_response = request_with_container(
        container,
        "POST",
        "/approvals/missing/approve",
    )
    reject_response = request_with_container(
        container,
        "POST",
        "/approvals/missing/reject",
    )

    assert get_response.status_code == 404
    assert get_response.json()["status"] == "not_found"
    assert approve_response.status_code == 404
    assert approve_response.json()["status"] == "not_found"
    assert reject_response.status_code == 404
    assert reject_response.json()["status"] == "not_found"


def test_approval_routes_are_secret_free() -> None:
    container = build_approval_container(callback_shared_secret="local-callback-secret")
    approval_id = create_pending_approval(container)

    response = request_with_container(container, "GET", f"/approvals/{approval_id}")

    assert response.status_code == 200
    assert "local-callback-secret" not in response.text
    assert "consumer_secret" not in response.text
    assert "passkey" not in response.text


def test_reject_writes_audit_event() -> None:
    container = build_approval_container()
    approval_id = create_pending_approval(container)

    response = request_with_container(
        container,
        "POST",
        f"/approvals/{approval_id}/reject",
    )

    assert response.status_code == 200
    assert container.audit_logger.events[-1].event_type == "approval_rejected"
    assert container.audit_logger.events[-1].payload["approval_id"] == approval_id


def build_approval_container(
    *,
    callback_shared_secret: str | None = None,
) -> AppContainer:
    return AppContainer.mock(
        settings=Settings(
            database_url="postgresql+asyncpg://mpesa:mpesa@localhost:5432/mpesa_mcp",
            callback_shared_secret=callback_shared_secret,
            operator_auth_enabled=False,
        )
    )


def create_pending_approval(container: AppContainer) -> str:
    response = container.payment_service.initiate_stk_push(
        phone_number="254700000000",
        amount=10_001,
        account_reference="INV-APPROVAL-001",
        description="Approval route test payment",
        idempotency_key=f"approval-route-{len(container.audit_logger.events)}",
    )

    assert response.approval_id is not None
    return response.approval_id


def request_with_container(
    container: AppContainer,
    method: str,
    path: str,
) -> Response:
    app.dependency_overrides[get_app_container] = lambda: container
    try:
        client = TestClient(app)
        return client.request(method, path)
    finally:
        app.dependency_overrides.clear()
