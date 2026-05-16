"""Integration tests for operator dashboard routes."""

from __future__ import annotations

from app.bootstrap.container import AppContainer
from app.callbacks.routes import get_app_container
from app.config import Settings
from app.main import app
from fastapi.testclient import TestClient
from httpx import Response


def test_list_transactions() -> None:
    container = build_operator_container()
    transaction_id = seed_transaction(container)

    response = request_with_container(container, "GET", "/operator/transactions")

    assert response.status_code == 200
    transactions = response.json()["transactions"]
    assert len(transactions) == 1
    assert transactions[0]["transaction_id"] == transaction_id
    assert transactions[0]["provider"] == "daraja"
    assert transactions[0]["rail"] == "mpesa"
    assert transactions[0]["status"] == "pending"
    assert transactions[0]["amount"] == 1_000
    assert transactions[0]["phone_number"] == "254700000000"
    assert "created_at" in transactions[0]


def test_get_transaction_by_id() -> None:
    container = build_operator_container()
    transaction_id = seed_transaction(container)

    response = request_with_container(
        container,
        "GET",
        f"/operator/transactions/{transaction_id}",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["transaction_id"] == transaction_id
    assert body["checkout_request_id"] == "ws_CO_OPERATOR"
    assert body["merchant_request_id"] == "mock_operator"


def test_missing_transaction_returns_not_found() -> None:
    container = build_operator_container()

    response = request_with_container(
        container,
        "GET",
        "/operator/transactions/missing",
    )

    assert response.status_code == 404
    assert response.json() == {
        "status": "not_found",
        "reason": "Transaction was not found.",
    }


def test_list_audit_events() -> None:
    container = build_operator_container()
    container.audit_logger.log_event(
        "operator_test_event",
        {"safe": True},
        actor="operator",
        correlation_id="corr-operator-001",
    )

    response = request_with_container(container, "GET", "/operator/audit-events")

    assert response.status_code == 200
    audit_events = response.json()["audit_events"]
    assert len(audit_events) == 1
    assert audit_events[0]["event_type"] == "operator_test_event"
    assert audit_events[0]["actor"] == "operator"
    assert audit_events[0]["correlation_id"] == "corr-operator-001"
    assert "created_at" in audit_events[0]
    assert "payload" not in audit_events[0]


def test_analytics_today_endpoint() -> None:
    container = build_operator_container()
    seed_transaction(container)

    response = request_with_container(container, "GET", "/operator/analytics/today")

    assert response.status_code == 200
    summary = response.json()["summary"]
    assert summary["total_transactions"] == 1
    assert summary["pending_transactions"] == 1
    assert summary["completed_transactions"] == 0
    assert summary["total_revenue"] == 0


def test_reconciliation_endpoint() -> None:
    container = build_operator_container()
    seed_transaction(container)

    response = request_with_container(
        container,
        "POST",
        "/operator/reconciliation/run",
    )

    assert response.status_code == 200
    summary = response.json()["summary"]
    assert summary["status"] == "ok"
    assert summary["checked_transactions"] == 1
    assert summary["finding_count"] >= 1
    assert summary["findings"][0]["transaction_id"]


def test_operator_routes_do_not_expose_secrets() -> None:
    container = build_operator_container(callback_shared_secret="local-callback-secret")
    seed_transaction(container)
    container.audit_logger.log_event(
        "operator_test_event",
        {"secret_like_value": "should-not-be-returned"},
        actor="operator",
    )

    transactions_response = request_with_container(
        container,
        "GET",
        "/operator/transactions",
    )
    audit_response = request_with_container(container, "GET", "/operator/audit-events")

    combined_body = transactions_response.text + audit_response.text
    assert "local-callback-secret" not in combined_body
    assert "should-not-be-returned" not in combined_body
    assert "consumer_secret" not in combined_body
    assert "passkey" not in combined_body


def build_operator_container(
    *,
    callback_shared_secret: str | None = None,
) -> AppContainer:
    return AppContainer.mock(
        settings=Settings(
            database_url="postgresql+asyncpg://mpesa:mpesa@localhost:5432/mpesa_mcp",
            callback_shared_secret=callback_shared_secret,
        )
    )


def seed_transaction(container: AppContainer) -> str:
    transaction = container.transaction_repository.save_pending_transaction(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-OPERATOR-001",
        description="Operator route test payment",
        checkout_request_id="ws_CO_OPERATOR",
        merchant_request_id="mock_operator",
        idempotency_key="operator-route-001",
        provider="daraja",
        rail="mpesa",
        provider_transaction_id="ws_CO_OPERATOR",
        provider_reference="mock_operator",
    )
    return transaction.transaction_id


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
