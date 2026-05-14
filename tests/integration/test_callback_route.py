"""Integration tests for callback routes."""

from __future__ import annotations

from typing import Any

from app.audit.logger import InMemoryAuditLogger
from app.callbacks.handlers import StkCallbackHandler
from app.callbacks.routes import get_stk_callback_handler
from app.main import app
from app.observability.metrics import InMemoryMetricsRecorder
from app.storage.repositories import InMemoryTransactionRepository
from fastapi.testclient import TestClient


def test_fastapi_callback_route_returns_expected_response() -> None:
    repository = InMemoryTransactionRepository()
    audit_logger = InMemoryAuditLogger()
    repository.save_pending_transaction(
        phone_number="254700000000",
        amount=1_000,
        account_reference="INV-001",
        description="Invoice payment",
        checkout_request_id="ws_CO_ROUTE",
        merchant_request_id="mock_123",
    )

    def override_handler() -> StkCallbackHandler:
        return StkCallbackHandler(
            transaction_repository=repository,
            audit_logger=audit_logger,
            metrics_recorder=InMemoryMetricsRecorder(),
        )

    app.dependency_overrides[get_stk_callback_handler] = override_handler

    try:
        client = TestClient(app)
        response = client.post("/callbacks/mpesa/stk", json=stk_callback_payload())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["success"] is True
    assert body["checkout_request_id"] == "ws_CO_ROUTE"

    transaction = repository.find_by_checkout_request_id("ws_CO_ROUTE")
    assert transaction is not None
    assert transaction.status == "completed"
    assert transaction.mpesa_receipt_number == "RCPROUTE"


def stk_callback_payload() -> dict[str, Any]:
    return {
        "Body": {
            "stkCallback": {
                "CheckoutRequestID": "ws_CO_ROUTE",
                "ResultCode": 0,
                "ResultDesc": "The service request is processed successfully.",
                "CallbackMetadata": {
                    "Item": [
                        {"Name": "Amount", "Value": 1_000},
                        {"Name": "MpesaReceiptNumber", "Value": "RCPROUTE"},
                        {"Name": "PhoneNumber", "Value": 254700000000},
                    ]
                },
            }
        }
    }
