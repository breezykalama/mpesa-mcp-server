"""Tests for approval workflow service."""

from __future__ import annotations

from app.approvals.repository import InMemoryApprovalRepository
from app.approvals.service import ApprovalService


def build_service() -> ApprovalService:
    return ApprovalService(approval_repository=InMemoryApprovalRepository())


def build_expiring_service() -> ApprovalService:
    return ApprovalService(
        approval_repository=InMemoryApprovalRepository(),
        expiry_minutes=0,
    )


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


def test_approval_created_with_expires_at() -> None:
    service = build_service()

    approval = service.create_approval_request(
        action="initiate_stk_push",
        payload={"amount": 20_000},
        reason="Amount exceeds limit.",
    )

    assert approval.expires_at > approval.created_at


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


def test_expired_approval_cannot_be_approved() -> None:
    service = build_expiring_service()
    approval = service.create_approval_request(
        action="initiate_stk_push",
        payload={"amount": 20_000},
        reason="Amount exceeds limit.",
    )

    response = service.approve_request(approval.approval_id)

    assert response.status == "expired"
    assert response.allowed is False
    assert response.approval is not None
    assert response.approval.status == "expired"
    assert response.approval.expired_at is not None


def test_expired_approval_cannot_be_rejected() -> None:
    service = build_expiring_service()
    approval = service.create_approval_request(
        action="initiate_stk_push",
        payload={"amount": 20_000},
        reason="Amount exceeds limit.",
    )

    response = service.reject_request(approval.approval_id)

    assert response.status == "expired"
    assert response.allowed is False
    assert response.approval is not None
    assert response.approval.status == "expired"


def test_stale_approvals_expire() -> None:
    service = build_expiring_service()
    approval = service.create_approval_request(
        action="initiate_stk_push",
        payload={"amount": 20_000},
        reason="Amount exceeds limit.",
    )

    expired_count = service.expire_stale_approvals()

    assert expired_count == 1
    expired_approval = service.get_approval_request(approval.approval_id)
    assert expired_approval is not None
    assert expired_approval.status == "expired"


def test_pending_list_excludes_expired_approvals() -> None:
    service = build_expiring_service()
    service.create_approval_request(
        action="initiate_stk_push",
        payload={"amount": 20_000},
        reason="Amount exceeds limit.",
    )

    assert service.list_pending_requests() == []
