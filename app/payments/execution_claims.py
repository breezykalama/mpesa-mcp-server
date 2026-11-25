"""Atomic durable payment execution claims (Slice E1).

An execution claim is the single durable grant that allows one internal worker to
execute the payment of one ``approved`` durable approval request. This module only
creates and reads claims. It never initiates, retries or confirms a provider payment,
and it is not exposed through MCP tools, HTTP routes or operator approval actions.

Authority boundary:

* The worker identity (``claimed_by``) is fixed when the repository is constructed by
  trusted internal wiring. ``claim_execution`` takes no caller-supplied identity, role
  or authority flag, so request data can never grant a claim.
* Nothing in the application container builds this repository yet.

There is deliberately no claim expiry, lease takeover or reassignment: once a worker
holds a claim, no other worker may assume the payment was not sent. Recovery of claims
whose provider outcome is uncertain belongs to a later slice.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.exc import DataError, IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.approvals.postgres_repository import (
    STORAGE_UNAVAILABLE_MESSAGE,
    UNIQUE_VIOLATION_SQLSTATE,
    ApprovalPersistenceError,
    ApprovalRequestNotFoundError,
    _begin_locked_transaction,
    _verify_payload_integrity,
)
from app.infrastructure.health import InfrastructureUnavailableError
from app.safety.policy import INITIATE_STK_PUSH
from app.storage.database import SessionFactory
from app.storage.models import (
    ApprovalDecisionModel,
    ApprovalRequestModel,
    PaymentExecutionClaimModel,
)

CLAIM_TOKEN_BYTES = 32
"""Entropy of the claim (fencing) token returned to the winning worker only."""
INITIAL_CLAIM_STATUS = "claimed"
EXECUTABLE_PAYMENT_ACTIONS: frozenset[str] = frozenset({INITIATE_STK_PUSH})
"""Approval actions that represent a payment the provider executes.

Reuses the safety policy's action vocabulary: ``initiate_stk_push`` is the only
payment-initiating action, and it is the only action ``PaymentService`` records on
approval requests (both MCP payment tools delegate to it). Read-only, unknown or
any other approved action never receives an execution claim. Migration
``0009_payment_execution_claims`` enforces the same set on insert.
"""
COUNTED_AUTHENTICATION_METHODS = frozenset({"oidc_jwks"})
"""Only IdP-verified decisions count towards quorum at claim time (matches D3)."""
MAX_WORKER_ID_LENGTH = 255


class ExecutionClaimRefusedError(ValueError):
    """Base class for claim attempts refused because of the approval's durable state."""


class ApprovalNotApprovedError(ExecutionClaimRefusedError):
    """Raised when the approval is pending, rejected or expired."""

    def __init__(self, status: str) -> None:
        super().__init__(f"Approval request is {status}, not approved.")
        self.status = status


class UnsupportedExecutionActionError(ExecutionClaimRefusedError):
    """Raised when the approved action is not a supported payment execution action."""


class ApprovalQuorumNotMetError(ExecutionClaimRefusedError):
    """Raised when persisted human decisions do not satisfy the snapshotted quorum."""


class ExecutionAlreadyClaimedError(ExecutionClaimRefusedError):
    """Raised when the approval already has an execution claim.

    It deliberately carries no claim ID, token or worker identity: a losing worker gets
    no handle on the winner's execution authority.
    """


class ExecutionClaimRecord(BaseModel):
    """Read-only view of a persisted claim. Never includes the claim token or its hash."""

    model_config = ConfigDict(frozen=True)

    claim_id: str
    approval_id: str
    payload_hash: str
    status: str
    claimed_by: str
    correlation_id: str | None
    claimed_at: datetime


@dataclass(frozen=True)
class ClaimOutcome:
    """The winning worker's claim and its fencing token.

    ``claim_token`` is returned exactly once, to the worker whose transaction created the
    claim; only its SHA-256 hash is stored. It is excluded from ``repr`` so it is not
    written to logs by accident.
    """

    claim: ExecutionClaimRecord
    claim_token: str = field(repr=False)


class PostgresExecutionClaimRepository:
    """Create and read durable payment execution claims for one internal worker."""

    def __init__(self, session_factory: SessionFactory, *, worker_id: str) -> None:
        """Create the repository for ``worker_id``, set by trusted internal wiring.

        ``worker_id`` is recorded as ``claimed_by`` on every claim this instance creates.
        It is attribution, not proof of authority: authority comes from holding this
        repository, which only internal execution infrastructure may construct.
        """

        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("worker_id must be a non-empty string")
        if worker_id != worker_id.strip() or len(worker_id) > MAX_WORKER_ID_LENGTH:
            raise ValueError("worker_id must be trimmed and at most 255 characters")
        self._session_factory = session_factory
        self._worker_id = worker_id

    def claim_execution(
        self,
        approval_id: str,
        *,
        correlation_id: str | None = None,
    ) -> ClaimOutcome:
        """Atomically create the one execution claim for an approved request.

        In a single READ COMMITTED transaction this:

        1. locks the approval row with ``SELECT ... FOR UPDATE`` (the same lock D3
           decisions and D4.3 expiry take), so concurrent claimers serialize in the
           database across processes and workers;
        2. verifies the request exists and its stored payload matches its hash;
        3. requires a supported payment action (``EXECUTABLE_PAYMENT_ACTIONS``) and
           persisted status ``approved``;
        4. re-derives quorum from persisted decisions: no rejection, and at least
           ``required_reviewers`` distinct verified human approvals bound to the stored
           payload hash;
        5. refuses if a claim already exists, then inserts the claim with a database
           timestamp, bound to the stored payload hash.

        The unique constraint on ``approval_id`` is the final guarantee of a single
        winner. Every refusal or failure rolls back: no partial claim is left and the
        approval request and its decisions are never modified. No payment provider is
        called.
        """

        try:
            with self._session_factory() as session, session.begin():
                _begin_locked_transaction(session)
                return self._claim_locked(session, approval_id, correlation_id=correlation_id)
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) == UNIQUE_VIOLATION_SQLSTATE:
                raise ExecutionAlreadyClaimedError(
                    "Approval request already has an execution claim."
                ) from exc
            raise ApprovalPersistenceError(
                "Execution claim violates a persistence constraint."
            ) from exc
        except DataError as exc:
            raise ApprovalPersistenceError("Execution claim data is invalid.") from exc
        except SQLAlchemyError as exc:
            raise InfrastructureUnavailableError(STORAGE_UNAVAILABLE_MESSAGE) from exc

    def get_claim(self, approval_id: str) -> ExecutionClaimRecord | None:
        """Return the persisted claim for ``approval_id``, or ``None`` if unclaimed."""

        try:
            with self._session_factory() as session:
                model = _load_claim(session, approval_id)
                return None if model is None else _to_claim_record(model)
        except SQLAlchemyError as exc:
            raise InfrastructureUnavailableError(STORAGE_UNAVAILABLE_MESSAGE) from exc

    def _claim_locked(
        self,
        session: Session,
        approval_id: str,
        *,
        correlation_id: str | None,
    ) -> ClaimOutcome:
        approval = session.scalars(
            select(ApprovalRequestModel)
            .where(ApprovalRequestModel.approval_id == approval_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        if approval is None:
            raise ApprovalRequestNotFoundError("Approval request not found.")

        _verify_payload_integrity(approval)
        if approval.action not in EXECUTABLE_PAYMENT_ACTIONS:
            raise UnsupportedExecutionActionError(
                "Approved action is not a supported payment execution action."
            )
        if approval.status != "approved":
            raise ApprovalNotApprovedError(approval.status)
        _verify_quorum(session, approval)

        if _load_claim(session, approval_id) is not None:
            raise ExecutionAlreadyClaimedError("Approval request already has an execution claim.")

        claim_token = secrets.token_urlsafe(CLAIM_TOKEN_BYTES)
        claim_id = str(uuid4())
        session.add(
            PaymentExecutionClaimModel(
                claim_id=claim_id,
                approval_id=approval_id,
                payload_hash=approval.payload_hash,
                status=INITIAL_CLAIM_STATUS,
                claim_token_hash=claim_token_hash(claim_token),
                claimed_by=self._worker_id,
                correlation_id=correlation_id,
                # clock_timestamp(), not now(): the transaction may have waited for the
                # row lock, and the claim time is when the claim was actually granted.
                claimed_at=func.clock_timestamp(),
            )
        )
        session.flush()

        created = session.scalars(
            select(PaymentExecutionClaimModel)
            .where(PaymentExecutionClaimModel.claim_id == claim_id)
            .execution_options(populate_existing=True)
        ).one()
        return ClaimOutcome(claim=_to_claim_record(created), claim_token=claim_token)


def claim_token_hash(claim_token: str) -> str:
    """Return the stored SHA-256 hex digest of a claim token."""

    return hashlib.sha256(claim_token.encode("utf-8")).hexdigest()


def _verify_quorum(session: Session, approval: ApprovalRequestModel) -> None:
    decisions = session.scalars(
        select(ApprovalDecisionModel).where(
            ApprovalDecisionModel.approval_id == approval.approval_id
        )
    ).all()
    if any(row.decision == "reject" for row in decisions):
        raise ApprovalQuorumNotMetError("Approval request has a rejection decision.")
    approvers = {
        (row.reviewer_issuer, row.reviewer_subject)
        for row in decisions
        if row.decision == "approve"
        and row.principal_type == "human"
        and row.authentication_method in COUNTED_AUTHENTICATION_METHODS
        and row.payload_hash == approval.payload_hash
    }
    if len(approvers) < approval.required_reviewers:
        raise ApprovalQuorumNotMetError(
            "Persisted human approvals do not satisfy the required reviewer quorum."
        )


def _load_claim(session: Session, approval_id: str) -> PaymentExecutionClaimModel | None:
    return session.scalars(
        select(PaymentExecutionClaimModel).where(
            PaymentExecutionClaimModel.approval_id == approval_id
        )
    ).one_or_none()


def _to_claim_record(model: PaymentExecutionClaimModel) -> ExecutionClaimRecord:
    return ExecutionClaimRecord(
        claim_id=model.claim_id,
        approval_id=model.approval_id,
        payload_hash=model.payload_hash,
        status=model.status,
        claimed_by=model.claimed_by,
        correlation_id=model.correlation_id,
        claimed_at=model.claimed_at.astimezone(UTC),
    )
