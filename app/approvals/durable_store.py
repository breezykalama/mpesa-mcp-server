"""Shared, non-executing durable approval creation, retrieval and expiry (D4.1, D4.3).

``DurableApprovalStore`` lets independent processes (the MCP server and the HTTP
operator API) create and read the same approval requests through PostgreSQL.

It is deliberately **isolated from the live approval workflow**:

* It exposes no approve or reject operation, and its repository trusts no reviewer
  issuer. Human decisions on durable approvals go only through
  ``DurableApprovalDecisionService`` (D4.2, ``/durable-approvals`` routes), which uses
  ``PostgresApprovalRepository.record_decision`` (D3) and never executes a payment.
* The live ``PaymentService`` / ``ApprovalService`` still create, decide and execute
  in-memory approvals. Nothing in the live flow writes to or reads from this store, so
  no approval request ever exists in both places and the two can never both accept
  decisions for the same request.
* It never calls a payment provider and nothing executes when an approval is created.
* Expiry (``expire_request`` / ``expire_due_requests``) is a database-clock lifecycle
  transition, not a decision: it writes no decision row and is attributed to no
  reviewer. Nothing schedules it automatically, and no MCP tool exposes it.

Switching the live workflow over (creation, decisions, execution) is a later slice; the
production ``APPROVAL_PERSISTENCE`` gate stays closed until then.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.approvals.models import ApprovalStatus
from app.approvals.postgres_repository import (
    DEFAULT_EXPIRY_BATCH_SIZE,
    DurableApprovalRecord,
    ExpiryOutcome,
    PostgresApprovalRepository,
)
from app.approvals.service import required_reviewers_for_payload
from app.config import Settings
from app.storage.database import SessionFactory

DURABLE_APPROVAL_POLICY_VERSION = "approval-policy/v1"
"""Version tag stored with every durable request's policy snapshot."""


class DurableApprovalStore:
    """Create and read durable approval requests shared across processes."""

    def __init__(
        self,
        repository: PostgresApprovalRepository,
        *,
        expiry_minutes: int,
        required_reviewers: int,
        high_risk_required_reviewers: int,
        high_risk_amount_threshold: int,
    ) -> None:
        self._repository = repository
        self._expiry_minutes = expiry_minutes
        self._required_reviewers = required_reviewers
        self._high_risk_required_reviewers = high_risk_required_reviewers
        self._high_risk_amount_threshold = high_risk_amount_threshold

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        session_factory: SessionFactory,
    ) -> DurableApprovalStore:
        """Build a store over ``session_factory`` using the configured approval policy.

        The repository is built without trusted reviewer issuers: this store records no
        decisions, and the repository therefore refuses every decision (fail closed).
        """

        return cls(
            PostgresApprovalRepository(session_factory),
            expiry_minutes=settings.approval_expiry_minutes,
            required_reviewers=settings.approval_required_reviewers,
            high_risk_required_reviewers=settings.high_risk_approval_required_reviewers,
            high_risk_amount_threshold=settings.high_risk_amount_threshold,
        )

    def create_approval_request(
        self,
        *,
        action: str,
        payload: dict[str, Any],
        reason: str,
        request_origin: str = "unspecified",
        correlation_id: str | None = None,
        approval_id: str | None = None,
    ) -> DurableApprovalRecord:
        """Persist a new pending approval request and return the committed record.

        The quorum, expiry and full policy are snapshotted at creation. The payload is
        validated and hashed canonically by the repository. An existing ``approval_id``
        raises ``DuplicateApprovalRequestError``; storage failures raise
        ``InfrastructureUnavailableError`` (never an in-memory fallback).
        """

        created_at = datetime.now(UTC)
        required_reviewers = required_reviewers_for_payload(
            payload,
            required_reviewers=self._required_reviewers,
            high_risk_required_reviewers=self._high_risk_required_reviewers,
            high_risk_amount_threshold=self._high_risk_amount_threshold,
        )
        return self._repository.create(
            action=action,
            payload=payload,
            reason=reason,
            created_at=created_at,
            expires_at=created_at + timedelta(minutes=self._expiry_minutes),
            required_reviewers=required_reviewers,
            policy_version=DURABLE_APPROVAL_POLICY_VERSION,
            policy_snapshot=self._policy_snapshot(required_reviewers),
            requested_by=request_origin,
            correlation_id=correlation_id,
            approval_id=approval_id,
        )

    def get_approval_request(self, approval_id: str) -> DurableApprovalRecord | None:
        """Return a durable approval request, or ``None`` if it does not exist."""

        return self._repository.get(approval_id)

    def list_requests(self, status: ApprovalStatus = "pending") -> list[DurableApprovalRecord]:
        """Return durable approval requests with ``status``, oldest first."""

        return self._repository.list_by_status(status)

    def expire_request(self, approval_id: str) -> ExpiryOutcome:
        """Expire one durable request if its database deadline has passed (idempotent).

        Approved, rejected, already-expired and not-yet-due requests are unchanged.
        Unknown IDs raise ``ApprovalRequestNotFoundError``.
        """

        return self._repository.expire_request(approval_id)

    def expire_due_requests(self, *, limit: int = DEFAULT_EXPIRY_BATCH_SIZE) -> int:
        """Expire up to ``limit`` overdue pending requests; return how many were expired.

        Safe to run from several workers at once (``FOR UPDATE SKIP LOCKED``).
        """

        return len(self._repository.expire_due_requests(limit=limit))

    def _policy_snapshot(self, required_reviewers: int) -> dict[str, Any]:
        return {
            "required_reviewers": required_reviewers,
            "base_required_reviewers": self._required_reviewers,
            "high_risk_required_reviewers": self._high_risk_required_reviewers,
            "high_risk_amount_threshold": self._high_risk_amount_threshold,
            "expiry_minutes": self._expiry_minutes,
            "decision_authority": "verified_human_oidc",
            "minimum_reviewer_role": "approver",
        }
