"""Isolated, non-executing human decisions on durable approvals (Slice D4.2).

``DurableApprovalDecisionService`` lets an authenticated human operator approve or
reject a durable (PostgreSQL) approval request through the D3
``PostgresApprovalRepository.record_decision`` path:

* The reviewer is always the server-authenticated ``OperatorIdentity``. Eligibility
  (verified OIDC JWKS human, approver role, trusted issuer) and distinct
  ``(issuer, subject)`` enforcement are applied by the repository, inside the same
  locked READ COMMITTED transaction that inserts the decision and applies the quorum.
* Trusted reviewer issuers come only from the validated OIDC configuration
  (``AUTH_MODE=oidc`` with ``OIDC_PROVIDER_MODE=jwks`` and ``OIDC_ISSUER``). Every
  other authentication mode trusts no issuer, so all decisions fail closed.
* Decisions are entirely separate from payment execution. This module imports no
  payment, provider or Daraja code, and reaching quorum only marks the durable request
  ``approved``; nothing executes it. Exactly-once durable execution is a later slice.

The live in-memory ``ApprovalService`` / ``PaymentService`` workflow is unchanged and
never sees durable approvals, so no request can be decided in both places.
"""

from __future__ import annotations

from app.approvals.postgres_repository import (
    DecisionOutcome,
    DecisionValue,
    PostgresApprovalRepository,
)
from app.auth.oidc import OperatorIdentity
from app.config import Settings
from app.storage.database import SessionFactory


def trusted_reviewer_issuers_from_settings(settings: Settings) -> frozenset[str]:
    """Return the OIDC issuers whose verified tokens may decide durable approvals.

    Only the JWKS provider verifies the token signature and ``iss`` claim against
    ``OIDC_ISSUER``, so that issuer is trusted only in that configuration. Disabled
    auth, static tokens and the development OIDC provider trust nothing.
    """

    if (
        not settings.operator_auth_enabled
        or settings.auth_mode != "oidc"
        or settings.oidc_provider_mode != "jwks"
        or settings.oidc_issuer is None
        or not settings.oidc_issuer.strip()
    ):
        return frozenset()
    return frozenset({settings.oidc_issuer})


class DurableApprovalDecisionService:
    """Record verified human decisions on durable approval requests (no execution)."""

    def __init__(self, repository: PostgresApprovalRepository) -> None:
        self._repository = repository

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        session_factory: SessionFactory,
    ) -> DurableApprovalDecisionService:
        """Build the service trusting only the validated OIDC JWKS issuer."""

        return cls(
            PostgresApprovalRepository(
                session_factory,
                trusted_reviewer_issuers=trusted_reviewer_issuers_from_settings(settings),
            )
        )

    def approve(
        self,
        approval_id: str,
        *,
        reviewer: OperatorIdentity,
        comment: str | None = None,
        correlation_id: str | None = None,
    ) -> DecisionOutcome:
        """Record an approval; quorum marks the request ``approved`` without executing it."""

        return self._decide(approval_id, "approve", reviewer, comment, correlation_id)

    def reject(
        self,
        approval_id: str,
        *,
        reviewer: OperatorIdentity,
        comment: str | None = None,
        correlation_id: str | None = None,
    ) -> DecisionOutcome:
        """Record a rejection; any valid rejection is terminal."""

        return self._decide(approval_id, "reject", reviewer, comment, correlation_id)

    def _decide(
        self,
        approval_id: str,
        decision: DecisionValue,
        reviewer: OperatorIdentity,
        comment: str | None,
        correlation_id: str | None,
    ) -> DecisionOutcome:
        return self._repository.record_decision(
            approval_id,
            reviewer=reviewer,
            decision=decision,
            comment=comment,
            correlation_id=correlation_id,
        )
