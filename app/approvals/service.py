"""Approval workflow service."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
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
        expiry_minutes: int = 30,
        required_reviewers: int = 1,
        high_risk_required_reviewers: int = 2,
        high_risk_amount_threshold: int = 50_000,
    ) -> None:
        self._approval_repository = approval_repository
        self._audit_logger = audit_logger
        self._expiry_minutes = expiry_minutes
        self._required_reviewers = required_reviewers
        self._high_risk_required_reviewers = high_risk_required_reviewers
        self._high_risk_amount_threshold = high_risk_amount_threshold

    def create_approval_request(
        self,
        *,
        action: str,
        payload: dict[str, Any],
        reason: str,
    ) -> ApprovalRequest:
        """Create an approval request."""

        created_at = datetime.now(UTC)
        required_reviewers = self._required_reviewers_for_payload(payload)
        approval = self._approval_repository.create(
            action=action,
            payload=payload,
            reason=reason,
            expires_at=created_at + timedelta(minutes=self._expiry_minutes),
            required_reviewers=required_reviewers,
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
                "required_reviewers": approval.required_reviewers,
                "reason": approval.reason,
            },
        )
        return approval

    def approve_request(
        self,
        approval_id: str,
        operator_id: str = "system_operator",
    ) -> ApprovalServiceResponse:
        """Approve an approval request."""

        current_approval = self._approval_repository.get(approval_id)
        if current_approval is not None and self.is_expired(current_approval):
            expired_approval = self._expire_approval(current_approval)
            return ApprovalServiceResponse(
                status="expired",
                allowed=False,
                reason="Approval request has expired.",
                approval=expired_approval,
            )

        if current_approval is None:
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

        if current_approval.status != "pending":
            return ApprovalServiceResponse(
                status="blocked",
                allowed=False,
                reason="Approval request is not pending.",
                approval=current_approval,
            )

        if operator_id in current_approval.reviewer_ids:
            logger.info(
                "Duplicate approval review blocked.",
                extra={
                    "event_type": "approval_duplicate_review_blocked",
                    "approval_id": approval_id,
                    "operator_id": operator_id,
                },
            )
            return ApprovalServiceResponse(
                status="blocked",
                allowed=False,
                reason="Operator has already approved this request.",
                approval=current_approval,
            )

        reviewer_ids = [*current_approval.reviewer_ids, operator_id]
        review_count = len(reviewer_ids)
        if review_count < current_approval.required_reviewers:
            approval = self._approval_repository.save(
                current_approval.model_copy(
                    update={
                        "reviewer_ids": reviewer_ids,
                        "review_count": review_count,
                        "reviewed_at": datetime.now(UTC),
                    }
                )
            )
            logger.info(
                "Approval request partially approved.",
                extra={
                    "event_type": "approval_partially_approved",
                    "approval_id": approval_id,
                    "review_count": review_count,
                    "required_reviewers": approval.required_reviewers,
                },
            )
            self._log_audit_event(
                "approval_partially_approved",
                {
                    "approval_id": approval_id,
                    "action": approval.action,
                    "review_count": review_count,
                    "required_reviewers": approval.required_reviewers,
                },
            )
            return ApprovalServiceResponse(
                status="partially_approved",
                allowed=True,
                reason="Approval review recorded; additional reviewer required.",
                approval=approval,
            )

        approval = self._approval_repository.save(
            current_approval.model_copy(
                update={
                    "status": "approved",
                    "reviewer_ids": reviewer_ids,
                    "review_count": review_count,
                    "reviewed_at": datetime.now(UTC),
                }
            )
        )
        logger.info(
            "Approval request approved.",
            extra={
                "event_type": "approval_approved",
                "approval_id": approval_id,
                "status": approval.status,
                "review_count": approval.review_count,
                "required_reviewers": approval.required_reviewers,
            },
        )
        self._log_audit_event(
            "approval_approved",
            {
                "approval_id": approval_id,
                "action": approval.action,
                "status": approval.status,
                "review_count": approval.review_count,
                "required_reviewers": approval.required_reviewers,
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

        current_approval = self._approval_repository.get(approval_id)
        if current_approval is not None and self.is_expired(current_approval):
            expired_approval = self._expire_approval(current_approval)
            return ApprovalServiceResponse(
                status="expired",
                allowed=False,
                reason="Approval request has expired.",
                approval=expired_approval,
            )

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

        self.expire_stale_approvals()
        return [
            approval
            for approval in self._approval_repository.list_pending()
            if not self.is_expired(approval)
        ]

    def expire_stale_approvals(self) -> int:
        """Expire stale pending approval requests and return the count."""

        expired_count = 0
        for approval in self._approval_repository.list_pending():
            if self.is_expired(approval):
                self._expire_approval(approval)
                expired_count += 1

        return expired_count

    def is_expired(self, approval: ApprovalRequest) -> bool:
        """Return whether an approval request is expired."""

        return approval.status == "pending" and datetime.now(UTC) >= approval.expires_at

    def _expire_approval(self, approval: ApprovalRequest) -> ApprovalRequest:
        expired_approval = self._approval_repository.update_status(
            approval_id=approval.approval_id,
            status="expired",
        )
        if expired_approval is None:
            return approval

        logger.info(
            "Approval request expired.",
            extra={
                "event_type": "approval_expired",
                "approval_id": approval.approval_id,
                "status": expired_approval.status,
            },
        )
        self._log_audit_event(
            "approval_expired",
            {
                "approval_id": approval.approval_id,
                "action": approval.action,
                "status": expired_approval.status,
            },
        )
        return expired_approval

    def _log_audit_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._audit_logger is None:
            return

        self._audit_logger.log_event(
            event_type,
            payload,
            actor="operator",
        )

    def _required_reviewers_for_payload(self, payload: dict[str, Any]) -> int:
        amount = payload.get("amount")
        if isinstance(amount, int) and amount >= self._high_risk_amount_threshold:
            return self._high_risk_required_reviewers

        return self._required_reviewers
