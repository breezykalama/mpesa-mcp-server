"""Approval workflow service."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from app.approvals.models import ApprovalRequest
from app.approvals.repository import ApprovalRepositoryProtocol

logger = logging.getLogger(__name__)


class ApprovalServiceResponse(BaseModel):
    """Structured approval service response."""

    status: str
    allowed: bool
    reason: str
    approval: ApprovalRequest | None = None


class ApprovalService:
    """Coordinate approval request lifecycle."""

    def __init__(self, *, approval_repository: ApprovalRepositoryProtocol) -> None:
        self._approval_repository = approval_repository

    def create_approval_request(
        self,
        *,
        action: str,
        payload: dict[str, Any],
        reason: str,
    ) -> ApprovalRequest:
        """Create an approval request."""

        approval = self._approval_repository.create(
            action=action,
            payload=payload,
            reason=reason,
        )
        logger.info(
            "Approval request created.",
            extra={
                "event_type": "approval_created",
                "approval_id": approval.approval_id,
                "status": approval.status,
            },
        )
        return approval

    def approve_request(self, approval_id: str) -> ApprovalServiceResponse:
        """Approve an approval request."""

        approval = self._approval_repository.update_status(
            approval_id=approval_id,
            status="approved",
        )
        if approval is None:
            logger.info(
                "Approval request not found.",
                extra={
                    "event_type": "approval_update_failed",
                    "approval_id": approval_id,
                    "status": "not_found",
                },
            )
            return ApprovalServiceResponse(
                status="not_found",
                allowed=False,
                reason="Approval request was not found.",
            )

        logger.info(
            "Approval request approved.",
            extra={
                "event_type": "approval_approved",
                "approval_id": approval_id,
                "status": approval.status,
            },
        )
        return ApprovalServiceResponse(
            status="approved",
            allowed=True,
            reason="Approval request approved.",
            approval=approval,
        )

    def reject_request(self, approval_id: str) -> ApprovalServiceResponse:
        """Reject an approval request."""

        approval = self._approval_repository.update_status(
            approval_id=approval_id,
            status="rejected",
        )
        if approval is None:
            logger.info(
                "Approval request not found.",
                extra={
                    "event_type": "approval_update_failed",
                    "approval_id": approval_id,
                    "status": "not_found",
                },
            )
            return ApprovalServiceResponse(
                status="not_found",
                allowed=False,
                reason="Approval request was not found.",
            )

        logger.info(
            "Approval request rejected.",
            extra={
                "event_type": "approval_rejected",
                "approval_id": approval_id,
                "status": approval.status,
            },
        )
        return ApprovalServiceResponse(
            status="rejected",
            allowed=False,
            reason="Approval request rejected.",
            approval=approval,
        )

    def get_approval_request(self, approval_id: str) -> ApprovalRequest | None:
        """Return an approval request by ID."""

        return self._approval_repository.get(approval_id)
