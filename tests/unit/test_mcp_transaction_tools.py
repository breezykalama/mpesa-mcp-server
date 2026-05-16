"""Tests for MCP transaction status tools."""

from __future__ import annotations

from app.mcp.tools import check_payment_status_tool, check_transaction_status_tool
from app.services.transaction_service import TransactionStatusServiceResponse


class RecordingTransactionService:
    """Transaction service test double that records delegation."""

    def __init__(self) -> None:
        self.called = False
        self.checkout_request_id: str | None = None

    def check_transaction_status(
        self,
        checkout_request_id: str,
    ) -> TransactionStatusServiceResponse:
        self.called = True
        self.checkout_request_id = checkout_request_id
        return TransactionStatusServiceResponse(
            status="completed",
            allowed=True,
            reason="The service request is processed successfully.",
            checkout_request_id=checkout_request_id,
            result_code="0",
            result_description="The service request is processed successfully.",
        )


def test_mcp_status_tool_success_response() -> None:
    service = RecordingTransactionService()

    response = check_transaction_status_tool(
        {"checkout_request_id": "ws_CO_123"},
        service,
    )

    assert response.status == "completed"
    assert response.allowed is True
    assert response.data["checkout_request_id"] == "ws_CO_123"
    assert response.data["result_code"] == "0"


def test_generic_payment_status_tool_success_response() -> None:
    service = RecordingTransactionService()

    response = check_payment_status_tool(
        {"provider_transaction_id": "provider_txn_123"},
        service,
    )

    assert response.status == "completed"
    assert response.allowed is True
    assert response.data["checkout_request_id"] == "provider_txn_123"
    assert service.checkout_request_id == "provider_txn_123"


def test_mcp_status_tool_invalid_input_handled_cleanly() -> None:
    service = RecordingTransactionService()

    response = check_transaction_status_tool({}, service)

    assert response.status == "invalid_input"
    assert response.allowed is False
    assert response.errors != []
    assert service.called is False


def test_mcp_status_tool_delegates_to_transaction_service() -> None:
    service = RecordingTransactionService()

    response = check_transaction_status_tool(
        {"checkout_request_id": "ws_CO_456"},
        service,
    )

    assert service.called is True
    assert service.checkout_request_id == "ws_CO_456"
    assert response.status == "completed"
