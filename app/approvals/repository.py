"""Approval repository interfaces and in-memory implementation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from app.approvals.models import ApprovalRequest, ApprovalStatus


class ApprovalRepositoryProtocol(Protocol):
    """Interface for approval persistence."""

    def create(
        self,
        *,
        action: str,
        payload: dict[str, object],
        reason: str,
    ) -> ApprovalRequest:
        """Create an approval request."""

    def get(self, approval_id: str) -> ApprovalRequest | None:
        """Return an approval request by ID."""

    def update_status(
        self,
        *,
        approval_id: str,
        status: ApprovalStatus,
    ) -> ApprovalRequest | None:
        """Update approval status."""


class InMemoryApprovalRepository:
    """In-memory approval repository."""

    def __init__(self) -> None:
        self._approvals: dict[str, ApprovalRequest] = {}

    def create(
        self,
        *,
        action: str,
        payload: dict[str, object],
        reason: str,
    ) -> ApprovalRequest:
        """Create an approval request."""

        approval = ApprovalRequest(
            approval_id=str(uuid4()),
            action=action,
            payload=payload,
            reason=reason,
        )
        self._approvals[approval.approval_id] = approval
        return approval

    def get(self, approval_id: str) -> ApprovalRequest | None:
        """Return an approval request by ID."""

        return self._approvals.get(approval_id)

    def update_status(
        self,
        *,
        approval_id: str,
        status: ApprovalStatus,
    ) -> ApprovalRequest | None:
        """Update approval status."""

        approval = self.get(approval_id)
        if approval is None:
            return None

        updated_approval = approval.model_copy(
            update={"status": status, "reviewed_at": datetime.now(UTC)}
        )
        self._approvals[approval_id] = updated_approval
        return updated_approval
