"""Tests for MCP receipt tools."""

from __future__ import annotations

from app.mcp.tools import generate_receipt_tool
from app.services.receipt_service import ReceiptServiceResponse


class RecordingReceiptService:
    """Receipt service test double that records delegation."""

    def __init__(self) -> None:
        self.called = False
        self.checkout_request_id: str | None = None

    def generate_receipt(self, checkout_request_id: str) -> ReceiptServiceResponse:
        self.called = True
        self.checkout_request_id = checkout_request_id
        return ReceiptServiceResponse(
            status="generated",
            allowed=True,
            reason="Receipt generated successfully.",
            receipt={"receipt_id": "receipt_123"},
        )


def test_mcp_receipt_tool_success_response() -> None:
    service = RecordingReceiptService()

    response = generate_receipt_tool({"checkout_request_id": "ws_CO_123"}, service)

    assert response.status == "generated"
    assert response.allowed is True
    assert response.data["receipt"]["receipt_id"] == "receipt_123"


def test_mcp_receipt_tool_invalid_input_handled_cleanly() -> None:
    service = RecordingReceiptService()

    response = generate_receipt_tool({}, service)

    assert response.status == "invalid_input"
    assert response.allowed is False
    assert response.errors != []
    assert service.called is False


def test_mcp_receipt_tool_delegates_to_receipt_service() -> None:
    service = RecordingReceiptService()

    response = generate_receipt_tool({"checkout_request_id": "ws_CO_456"}, service)

    assert service.called is True
    assert service.checkout_request_id == "ws_CO_456"
    assert response.status == "generated"
