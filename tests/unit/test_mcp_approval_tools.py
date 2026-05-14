"""Tests for MCP approval tools."""

from __future__ import annotations

from app.approvals.models import ApprovalRequest
from app.approvals.service import ApprovalServiceResponse
from app.mcp.tools import approve_payment_request_tool, reject_payment_request_tool
from app.services.payment_service import ApprovalExecutionResponse, PaymentResponse


class RecordingApprovalService:
    """Approval service test double that records delegation."""

    def __init__(self) -> None:
        self.approved_id: str | None = None
        self.rejected_id: str | None = None

    def execute_approved_payment(self, approval_id: str) -> ApprovalExecutionResponse:
        self.approved_id = approval_id
        return ApprovalExecutionResponse(
            status="approved",
            allowed=True,
            reason="Approval request approved and payment execution attempted.",
            approval=ApprovalRequest(
                approval_id=approval_id,
                action="initiate_stk_push",
                payload={"amount": 20_000},
                reason="Amount exceeds limit.",
                status="approved",
            ),
            payment=PaymentResponse(
                status="pending",
                allowed=True,
                reason="STK push initiated successfully.",
                transaction_id="txn_123",
            ),
        )

    def reject_request(self, approval_id: str) -> ApprovalServiceResponse:
        self.rejected_id = approval_id
        return ApprovalServiceResponse(
            status="rejected",
            allowed=False,
            reason="Approval request rejected.",
            approval=ApprovalRequest(
                approval_id=approval_id,
                action="initiate_stk_push",
                payload={"amount": 20_000},
                reason="Amount exceeds limit.",
                status="rejected",
            ),
        )


def test_approve_payment_request_tool_delegates() -> None:
    service = RecordingApprovalService()

    response = approve_payment_request_tool({"approval_id": "approval-123"}, service)

    assert service.approved_id == "approval-123"
    assert response.status == "approved"
    assert response.allowed is True
    assert response.data["approval"]["approval_id"] == "approval-123"
    assert response.data["payment"]["transaction_id"] == "txn_123"


def test_reject_payment_request_tool_delegates() -> None:
    service = RecordingApprovalService()

    response = reject_payment_request_tool({"approval_id": "approval-456"}, service)

    assert service.rejected_id == "approval-456"
    assert response.status == "rejected"
    assert response.allowed is False
    assert response.data["approval"]["approval_id"] == "approval-456"
