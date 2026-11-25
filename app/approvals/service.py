"""Approval workflow service.

Approval decisions (approve and reject) are human-only. Every decision requires an
``OperatorIdentity`` produced by server-side authentication. There is intentionally no
default or synthetic reviewer identity.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel

from app.approvals.models import ApprovalRequest
from app.approvals.repository import ApprovalRepositoryProtocol
from app.audit.logger import AuditLoggerProtocol
from app.auth.oidc import AuthenticationMethod, OperatorIdentity

logger = logging.getLogger(__name__)

ApprovalDecision = Literal["approve", "reject"]

REVIEWER_NOT_AUTHORIZED_STATUS = "reviewer_not_authorized"
"""Service status returned when the authenticated operator may not make the decision."""

DECISION_ACTOR = "authenticated_operator"
"""Audit actor for decisions taken by a server-authenticated operator."""

SYSTEM_ACTOR = "system"
"""Audit actor for system-driven lifecycle changes such as expiry."""

DEVELOPMENT_AUTHENTICATION_METHODS: frozenset[AuthenticationMethod] = frozenset(
    {"static_token", "oidc_development", "auth_disabled"}
)
"""Authentication methods eligible for the development-only single-reviewer exception.

The exception additionally requires ``allows_development_approval_authority`` to be
true, which is never the case when ``DARAJA_MODE=production``.
"""

MAX_AUDITED_APPROVAL_ID_LENGTH = 64
"""Caller-supplied approval IDs are truncated before logging or auditing."""


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
        allow_development_reviewers: bool = False,
    ) -> None:
        self._approval_repository = approval_repository
        self._audit_logger = audit_logger
        self._expiry_minutes = expiry_minutes
        self._required_reviewers = required_reviewers
        self._high_risk_required_reviewers = high_risk_required_reviewers
        self._high_risk_amount_threshold = high_risk_amount_threshold
        self._allow_development_reviewers = allow_development_reviewers
        # Process-local serialization of approval decisions. This prevents concurrent
        # in-process decisions on the same approval from interleaving. It is NOT a
        # cross-process or cross-worker guarantee; that requires durable, atomic
        # approval persistence.
        self._decision_lock = threading.RLock()

    @property
    def decision_lock(self) -> threading.RLock:
        """Return the process-local lock that serializes approval decisions."""

        return self._decision_lock

    def create_approval_request(
        self,
        *,
        action: str,
        payload: dict[str, Any],
        reason: str,
        request_origin: str = "unspecified",
    ) -> ApprovalRequest:
        """Create an approval request.

        ``request_origin`` records who asked for the action (for example ``mcp_agent``).
        A requester never gains authority to decide its own request.
        """

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
                "request_origin": request_origin,
            },
            actor=request_origin,
        )
        return approval

    def approve_request(
        self,
        approval_id: str,
        *,
        reviewer: OperatorIdentity,
    ) -> ApprovalServiceResponse:
        """Record an approval review from an authenticated operator."""

        with self._decision_lock:
            return self._approve_request_locked(approval_id, reviewer=reviewer)

    def reject_request(
        self,
        approval_id: str,
        *,
        reviewer: OperatorIdentity,
    ) -> ApprovalServiceResponse:
        """Reject a pending approval request on behalf of an authenticated operator."""

        with self._decision_lock:
            return self._reject_request_locked(approval_id, reviewer=reviewer)

    def authorize_decision(
        self,
        approval_id: str,
        *,
        reviewer: OperatorIdentity,
        decision: ApprovalDecision,
    ) -> ApprovalServiceResponse | None:
        """Return a denial if the reviewer may not make this decision, else ``None``.

        Authorization runs before any approval state handling, in two stages:

        1. Principal eligibility (role, application identity, verified human) is checked
           without looking up the approval at all.
        2. The multi-reviewer quorum rule needs the request's ``required_reviewers`` and
           therefore performs a read-only lookup. It never expires, saves, or otherwise
           mutates approval state.

        Denials never include approval details and never mutate state. Each denial is
        audited once with the authenticated reviewer identity.
        """

        principal_reason = self._principal_denial_reason(reviewer)
        if principal_reason is not None:
            return self._deny_decision(
                approval_id,
                reviewer=reviewer,
                decision=decision,
                reason=principal_reason,
            )

        approval = self._approval_repository.get(approval_id)
        if approval is None:
            return None

        quorum_reason = self._quorum_denial_reason(reviewer, approval, decision=decision)
        if quorum_reason is not None:
            return self._deny_decision(
                approval_id,
                reviewer=reviewer,
                decision=decision,
                reason=quorum_reason,
            )

        return None

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

        with self._decision_lock:
            expired_count = 0
            for approval in self._approval_repository.list_pending():
                if self.is_expired(approval):
                    self._expire_approval(approval)
                    expired_count += 1

            return expired_count

    def is_expired(self, approval: ApprovalRequest) -> bool:
        """Return whether an approval request is expired."""

        return approval.status == "pending" and datetime.now(UTC) >= approval.expires_at

    def _approve_request_locked(
        self,
        approval_id: str,
        *,
        reviewer: OperatorIdentity,
    ) -> ApprovalServiceResponse:
        denial = self.authorize_decision(approval_id, reviewer=reviewer, decision="approve")
        if denial is not None:
            return denial

        current_approval = self._approval_repository.get(approval_id)
        if current_approval is None:
            return self._not_found_response(approval_id)

        if self.is_expired(current_approval):
            return self._expired_response(current_approval)

        if current_approval.status != "pending":
            return self._not_pending_response(
                current_approval,
                reviewer=reviewer,
                decision="approve",
            )

        if reviewer.subject in current_approval.reviewer_ids:
            logger.info(
                "Duplicate approval review blocked.",
                extra={
                    "event_type": "approval_duplicate_review_blocked",
                    "approval_id": approval_id,
                    "operator_id": reviewer.subject,
                },
            )
            self._log_decision_event(
                "approval_duplicate_review_blocked",
                current_approval,
                reviewer=reviewer,
                extra={"reason": "Operator has already approved this request."},
            )
            return ApprovalServiceResponse(
                status="blocked",
                allowed=False,
                reason="Operator has already approved this request.",
                approval=current_approval,
            )

        reviewer_ids = [*current_approval.reviewer_ids, reviewer.subject]
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
            self._log_decision_event(
                "approval_partially_approved",
                approval,
                reviewer=reviewer,
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
        self._log_decision_event("approval_approved", approval, reviewer=reviewer)
        return ApprovalServiceResponse(
            status="approved",
            allowed=True,
            reason="Approval request approved.",
            approval=approval,
        )

    def _reject_request_locked(
        self,
        approval_id: str,
        *,
        reviewer: OperatorIdentity,
    ) -> ApprovalServiceResponse:
        denial = self.authorize_decision(approval_id, reviewer=reviewer, decision="reject")
        if denial is not None:
            return denial

        current_approval = self._approval_repository.get(approval_id)
        if current_approval is None:
            return self._not_found_response(approval_id)

        if self.is_expired(current_approval):
            return self._expired_response(current_approval)

        if current_approval.status != "pending":
            return self._not_pending_response(
                current_approval,
                reviewer=reviewer,
                decision="reject",
            )

        approval = self._approval_repository.update_status(
            approval_id=approval_id,
            status="rejected",
        )
        if approval is None:
            return self._not_found_response(approval_id)

        logger.info(
            "Approval request rejected.",
            extra={
                "event_type": "approval_rejected",
                "approval_id": approval_id,
                "status": approval.status,
            },
        )
        self._log_decision_event("approval_rejected", approval, reviewer=reviewer)
        return ApprovalServiceResponse(
            status="rejected",
            allowed=False,
            reason="Approval request rejected.",
            approval=approval,
        )

    def _principal_denial_reason(self, reviewer: OperatorIdentity) -> str | None:
        """Return why a principal may never decide, independent of any approval."""

        if not reviewer.has_role("approver"):
            return "Operator role is not permitted to review approvals."

        if reviewer.principal_type == "application":
            return "Application identities cannot make approval decisions."

        if reviewer.is_verified_human:
            return None

        # The development exception is an allowlist of explicitly development-only
        # authentication methods. Identity-provider (JWKS) principals that are not
        # positively classified as delegated humans never receive it: a valid JWT alone
        # does not prove a human made the decision.
        if reviewer.authentication_method not in DEVELOPMENT_AUTHENTICATION_METHODS:
            return (
                "Identity-provider principals must be verified human operators to make "
                "approval decisions."
            )

        if not self._allow_development_reviewers:
            return "Approval decisions require a verified human operator identity."

        return None

    def _quorum_denial_reason(
        self,
        reviewer: OperatorIdentity,
        approval: ApprovalRequest,
        *,
        decision: ApprovalDecision,
    ) -> str | None:
        """Return why an unverified identity may not count toward a quorum."""

        if reviewer.is_verified_human:
            return None

        if decision == "approve" and approval.required_reviewers > 1:
            return (
                "Multi-reviewer approvals require verified human operator identities; "
                "shared or development identities cannot prove distinct reviewers."
            )

        return None

    def _deny_decision(
        self,
        approval_id: str,
        *,
        reviewer: OperatorIdentity,
        decision: ApprovalDecision,
        reason: str,
    ) -> ApprovalServiceResponse:
        """Audit a denied decision attempt without touching approval state."""

        recorded_approval_id = approval_id[:MAX_AUDITED_APPROVAL_ID_LENGTH]
        logger.warning(
            "Approval decision denied.",
            extra={
                "event_type": "approval_decision_denied",
                "approval_id": recorded_approval_id,
                "operator_id": reviewer.subject,
                "decision": decision,
            },
        )
        self._log_audit_event(
            "approval_decision_denied",
            {
                "approval_id": recorded_approval_id,
                "decision": decision,
                "reason": reason,
                "decision_authority": "human_operator",
                "authentication_method": reviewer.authentication_method,
                "principal_type": reviewer.principal_type,
                "reviewer_assurance": (
                    "verified_human" if reviewer.is_verified_human else "unverified"
                ),
            },
            actor=DECISION_ACTOR,
            identity=reviewer,
        )
        return ApprovalServiceResponse(
            status=REVIEWER_NOT_AUTHORIZED_STATUS,
            allowed=False,
            reason=reason,
        )

    def _not_pending_response(
        self,
        approval: ApprovalRequest,
        *,
        reviewer: OperatorIdentity,
        decision: ApprovalDecision,
    ) -> ApprovalServiceResponse:
        logger.info(
            "Approval decision blocked; request is not pending.",
            extra={
                "event_type": "approval_decision_blocked",
                "approval_id": approval.approval_id,
                "status": approval.status,
                "decision": decision,
            },
        )
        self._log_decision_event(
            "approval_decision_blocked",
            approval,
            reviewer=reviewer,
            extra={"decision": decision, "reason": "Approval request is not pending."},
        )
        return ApprovalServiceResponse(
            status="blocked",
            allowed=False,
            reason="Approval request is not pending.",
            approval=approval,
        )

    def _not_found_response(self, approval_id: str) -> ApprovalServiceResponse:
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

    def _expired_response(self, approval: ApprovalRequest) -> ApprovalServiceResponse:
        expired_approval = self._expire_approval(approval)
        return ApprovalServiceResponse(
            status="expired",
            allowed=False,
            reason="Approval request has expired.",
            approval=expired_approval,
        )

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
            actor=SYSTEM_ACTOR,
        )
        return expired_approval

    def _log_decision_event(
        self,
        event_type: str,
        approval: ApprovalRequest,
        *,
        reviewer: OperatorIdentity,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Audit a decision or decision attempt attributed to the authenticated operator.

        Only normalized identity fields are recorded. Credentials, bearer tokens, and
        raw identity-provider claims are never part of the audit payload.
        """

        payload: dict[str, Any] = {
            "approval_id": approval.approval_id,
            "action": approval.action,
            "status": approval.status,
            "review_count": approval.review_count,
            "required_reviewers": approval.required_reviewers,
            "reviewer_ids": list(approval.reviewer_ids),
            "decision_authority": "human_operator",
            "authentication_method": reviewer.authentication_method,
            "principal_type": reviewer.principal_type,
            "reviewer_assurance": (
                "verified_human" if reviewer.is_verified_human else "unverified"
            ),
        }
        if extra:
            payload.update(extra)

        self._log_audit_event(
            event_type,
            payload,
            actor=DECISION_ACTOR,
            identity=reviewer,
        )

    def _log_audit_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        actor: str,
        identity: OperatorIdentity | None = None,
    ) -> None:
        if self._audit_logger is None:
            return

        self._audit_logger.log_event(
            event_type,
            payload,
            actor=actor,
            identity=identity,
        )

    def _required_reviewers_for_payload(self, payload: dict[str, Any]) -> int:
        return required_reviewers_for_payload(
            payload,
            required_reviewers=self._required_reviewers,
            high_risk_required_reviewers=self._high_risk_required_reviewers,
            high_risk_amount_threshold=self._high_risk_amount_threshold,
        )


def required_reviewers_for_payload(
    payload: dict[str, Any],
    *,
    required_reviewers: int,
    high_risk_required_reviewers: int,
    high_risk_amount_threshold: int,
) -> int:
    """Return the reviewer quorum for a payment payload (shared by every approval store)."""

    amount = payload.get("amount")
    if isinstance(amount, int) and amount >= high_risk_amount_threshold:
        return high_risk_required_reviewers

    return required_reviewers
