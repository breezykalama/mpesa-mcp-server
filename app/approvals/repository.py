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
        expires_at: datetime,
        required_reviewers: int = 1,
    ) -> ApprovalRequest:
        """Create an approval request."""

    def get(self, approval_id: str) -> ApprovalRequest | None:
        """Return an approval request by ID."""

    def list_pending(self) -> list[ApprovalRequest]:
        """Return pending approval requests."""

    def update_status(
        self,
        *,
        approval_id: str,
        status: ApprovalStatus,
    ) -> ApprovalRequest | None:
        """Update approval status."""

    def save(self, approval: ApprovalRequest) -> ApprovalRequest:
        """Persist and return an approval request."""


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
        expires_at: datetime,
        required_reviewers: int = 1,
    ) -> ApprovalRequest:
        """Create an approval request."""

        approval = ApprovalRequest(
            approval_id=str(uuid4()),
            action=action,
            payload=payload,
            reason=reason,
            expires_at=expires_at,
            required_reviewers=required_reviewers,
        )
        self._approvals[approval.approval_id] = approval
        return approval

    def get(self, approval_id: str) -> ApprovalRequest | None:
        """Return an approval request by ID."""

        return self._approvals.get(approval_id)

    def list_pending(self) -> list[ApprovalRequest]:
        """Return pending approval requests."""

        return [
            approval
            for approval in self._approvals.values()
            if approval.status == "pending"
        ]

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

        now = datetime.now(UTC)
        update_payload: dict[str, object] = {"status": status}
        if status == "expired":
            update_payload["expired_at"] = now
        else:
            update_payload["reviewed_at"] = now

        updated_approval = approval.model_copy(update=update_payload)
        self._approvals[approval_id] = updated_approval
        return updated_approval

    def save(self, approval: ApprovalRequest) -> ApprovalRequest:
        """Persist and return an approval request."""

        self._approvals[approval.approval_id] = approval
        return approval
