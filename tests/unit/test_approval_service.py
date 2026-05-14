"""Tests for approval workflow service."""

from __future__ import annotations

from app.approvals.repository import InMemoryApprovalRepository
from app.approvals.service import ApprovalService


def build_service() -> ApprovalService:
    return ApprovalService(approval_repository=InMemoryApprovalRepository())


def test_approval_can_be_approved() -> None:
    service = build_service()
    approval = service.create_approval_request(
        action="initiate_stk_push",
        payload={"amount": 20_000},
        reason="Amount exceeds limit.",
    )

    response = service.approve_request(approval.approval_id)

    assert response.status == "approved"
    assert response.allowed is True
    assert response.approval is not None
    assert response.approval.status == "approved"
    assert response.approval.reviewed_at is not None


def test_approval_can_be_rejected() -> None:
    service = build_service()
    approval = service.create_approval_request(
        action="initiate_stk_push",
        payload={"amount": 20_000},
        reason="Amount exceeds limit.",
    )

    response = service.reject_request(approval.approval_id)

    assert response.status == "rejected"
    assert response.allowed is False
    assert response.approval is not None
    assert response.approval.status == "rejected"
    assert response.approval.reviewed_at is not None
