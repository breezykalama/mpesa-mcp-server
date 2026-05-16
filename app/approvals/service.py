"""Approval workflow service."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from app.approvals.models import ApprovalRequest
from app.approvals.repository import ApprovalRepositoryProtocol
from app.audit.logger import AuditLoggerProtocol

logger = logging.getLogger(__name__)


class ApprovalServiceResponse(BaseModel):
    """Structured approval service response."""

    status: str
    allowed: bool
    reason: str
    approval: ApprovalRequest | None = None


class ApprovalService:
    """Coordinate approval request lifecycle."""

    def __init__(
        self,
        *,
        approval_repository: ApprovalRepositoryProtocol,
        audit_logger: AuditLoggerProtocol | None = None,
    ) -> None:
        self._approval_repository = approval_repository
        self._audit_logger = audit_logger

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
        self._log_audit_event(
            "approval_created",
            {
                "approval_id": approval.approval_id,
                "action": approval.action,
                "status": approval.status,
                "reason": approval.reason,
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
        self._log_audit_event(
            "approval_approved",
            {
                "approval_id": approval_id,
                "action": approval.action,
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
        self._log_audit_event(
            "approval_rejected",
            {
                "approval_id": approval_id,
                "action": approval.action,
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

    def list_pending_requests(self) -> list[ApprovalRequest]:
        """Return pending approval requests."""

        return self._approval_repository.list_pending()

    def _log_audit_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._audit_logger is None:
            return

        self._audit_logger.log_event(
            event_type,
            payload,
            actor="operator",
        )
