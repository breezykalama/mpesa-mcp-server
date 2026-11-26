"""Persistence models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

DocumentJSON = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")
"""JSON document column type: JSONB on PostgreSQL, generic JSON elsewhere.

``none_as_null`` stores Python ``None`` as SQL ``NULL`` (not JSON ``null``) so that
``NOT NULL`` constraints cannot be bypassed with a JSON null document.
"""


class Base(DeclarativeBase):
    """Base class for persistence models."""


class TransactionModel(Base):
    """Transaction persistence model."""

    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'completed', 'failed', 'timed_out', 'cancelled')",
            name="ck_transactions_status_allowed",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_transactions_idempotency_key",
        ),
        UniqueConstraint(
            "provider_transaction_id",
            name="uq_transactions_provider_transaction_id",
        ),
        Index("ix_transactions_provider_status", "provider", "status"),
    )

    transaction_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="daraja", index=True)
    rail: Mapped[str] = mapped_column(String(64), nullable=False, default="mpesa", index=True)
    provider_transaction_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )
    provider_reference: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )
    checkout_request_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        unique=True,
        index=True,
    )
    merchant_request_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    phone_number: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    account_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True, default="pending")
    result_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mpesa_receipt_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class AuditEventModel(Base):
    """Audit event persistence model."""

    __tablename__ = "audit_events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        index=True,
    )
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    operator_subject: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    operator_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    operator_display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    operator_roles: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)


class ApprovalRequestModel(Base):
    """Durable approval request (schema foundation; not yet wired into the runtime).

    The payment payload, its integrity hash, the policy snapshot and the reviewer quorum
    are captured once at creation. On PostgreSQL, migration ``0008_durable_approvals``
    installs triggers that reject any later change to those columns, reject reopening a
    resolved request, and reject deletion of requests.
    """

    __tablename__ = "approval_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired')",
            name="ck_approval_requests_status_allowed",
        ),
        CheckConstraint(
            "required_reviewers BETWEEN 1 AND 10",
            name="ck_approval_requests_required_reviewers_range",
        ),
        CheckConstraint(
            "payload_hash_algorithm IN ('sha256')",
            name="ck_approval_requests_payload_hash_algorithm_allowed",
        ),
        CheckConstraint(
            "length(payload_hash) = 64 AND payload_hash = lower(payload_hash)",
            name="ck_approval_requests_payload_hash_format",
        ),
        CheckConstraint(
            "length(action) > 0",
            name="ck_approval_requests_action_not_empty",
        ),
        CheckConstraint(
            "length(policy_version) > 0",
            name="ck_approval_requests_policy_version_not_empty",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_approval_requests_expiry_after_creation",
        ),
        CheckConstraint(
            "(status = 'pending' AND resolved_at IS NULL)"
            " OR (status <> 'pending' AND resolved_at IS NOT NULL)",
            name="ck_approval_requests_resolution_consistent",
        ),
        CheckConstraint("version >= 1", name="ck_approval_requests_version_positive"),
        UniqueConstraint(
            "approval_id",
            "payload_hash",
            name="uq_approval_requests_approval_id_payload_hash",
        ),
        Index("ix_approval_requests_status_expires_at", "status", "expires_at"),
    )

    approval_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    payment_payload: Mapped[dict[str, Any]] = mapped_column(DocumentJSON, nullable=False)
    payload_hash_algorithm: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="sha256",
    )
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(DocumentJSON, nullable=False)
    required_reviewers: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": version}


class ApprovalDecisionModel(Base):
    """Append-only record of one reviewer's approve or reject decision.

    Each reviewer, identified by ``(reviewer_issuer, reviewer_subject)``, may decide an
    approval at most once. The composite foreign key on ``(approval_id, payload_hash)``
    binds every decision to the exact payload hash of its approval request. On
    PostgreSQL, triggers make these rows immutable (no UPDATE or DELETE).
    """

    __tablename__ = "approval_decisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["approval_id", "payload_hash"],
            ["approval_requests.approval_id", "approval_requests.payload_hash"],
            name="fk_approval_decisions_approval_payload_hash",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "approval_id",
            "reviewer_issuer",
            "reviewer_subject",
            name="uq_approval_decisions_reviewer",
        ),
        CheckConstraint(
            "decision IN ('approve', 'reject')",
            name="ck_approval_decisions_decision_allowed",
        ),
        CheckConstraint(
            "length(reviewer_issuer) > 0",
            name="ck_approval_decisions_reviewer_issuer_not_empty",
        ),
        CheckConstraint(
            "length(reviewer_subject) > 0",
            name="ck_approval_decisions_reviewer_subject_not_empty",
        ),
        CheckConstraint(
            "authentication_method IN"
            " ('oidc_jwks', 'oidc_development', 'static_token', 'auth_disabled')",
            name="ck_approval_decisions_authentication_method_allowed",
        ),
        CheckConstraint(
            "principal_type IN ('human', 'application', 'unknown')",
            name="ck_approval_decisions_principal_type_allowed",
        ),
        Index(
            "ix_approval_decisions_reviewer",
            "reviewer_issuer",
            "reviewer_subject",
        ),
    )

    decision_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    approval_id: Mapped[str] = mapped_column(String(36), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reviewer_issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    reviewer_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    reviewer_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    reviewer_display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reviewer_roles: Mapped[list[str]] = mapped_column(DocumentJSON, nullable=False)
    authentication_method: Mapped[str] = mapped_column(String(32), nullable=False)
    principal_type: Mapped[str] = mapped_column(String(16), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class PaymentExecutionClaimModel(Base):
    """Durable claim granting one internal worker the right to execute one approval.

    Exactly one claim may exist per approval, bound by composite foreign key to the
    approval's stored payload hash. Only a SHA-256 hash of the claim token is stored.
    Execution state is kept here, never in ``approval_requests``. On PostgreSQL,
    migration ``0009_payment_execution_claims`` installs triggers that refuse claims
    for approvals that are not ``approved`` payment actions, keep claim identity, binding and
    attribution immutable, and reject deletion.
    """

    __tablename__ = "payment_execution_claims"
    __table_args__ = (
        ForeignKeyConstraint(
            ["approval_id", "payload_hash"],
            ["approval_requests.approval_id", "approval_requests.payload_hash"],
            name="fk_payment_execution_claims_approval_payload_hash",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("approval_id", name="uq_payment_execution_claims_approval_id"),
        UniqueConstraint(
            "claim_token_hash",
            name="uq_payment_execution_claims_claim_token_hash",
        ),
        UniqueConstraint(
            "claim_id",
            "approval_id",
            "payload_hash",
            name="uq_payment_execution_claims_identity",
        ),
        CheckConstraint(
            "status IN ('claimed')",
            name="ck_payment_execution_claims_status_allowed",
        ),
        CheckConstraint(
            "length(claim_token_hash) = 64 AND claim_token_hash = lower(claim_token_hash)",
            name="ck_payment_execution_claims_token_hash_format",
        ),
        CheckConstraint(
            "length(claimed_by) > 0",
            name="ck_payment_execution_claims_claimed_by_not_empty",
        ),
    )

    claim_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    approval_id: Mapped[str] = mapped_column(String(36), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    claim_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    claimed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class PaymentExecutionAttemptModel(Base):
    """At most one execution attempt per claim, recorded by the claim owner (E2.1).

    Bound by composite foreign key to the claim, approval and approved payload hash.
    On PostgreSQL, migration ``0010_payment_execution_evidence`` makes attempts
    append-only and requires ``recorded_by`` to be the claim owner.
    """

    __tablename__ = "payment_execution_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["claim_id", "approval_id", "payload_hash"],
            [
                "payment_execution_claims.claim_id",
                "payment_execution_claims.approval_id",
                "payment_execution_claims.payload_hash",
            ],
            name="fk_payment_execution_attempts_claim_identity",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("claim_id", name="uq_payment_execution_attempts_claim_id"),
        UniqueConstraint(
            "attempt_id",
            "claim_id",
            "approval_id",
            "payload_hash",
            name="uq_payment_execution_attempts_identity",
        ),
        CheckConstraint(
            "length(provider) > 0 AND length(rail) > 0",
            name="ck_payment_execution_attempts_provider_not_empty",
        ),
        CheckConstraint(
            "length(recorded_by) > 0",
            name="ck_payment_execution_attempts_recorded_by_not_empty",
        ),
    )

    attempt_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    claim_id: Mapped[str] = mapped_column(String(36), nullable=False)
    approval_id: Mapped[str] = mapped_column(String(36), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    rail: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    recorded_by: Mapped[str] = mapped_column(String(255), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class PaymentExecutionEvidenceModel(Base):
    """Append-only fact observed about one execution attempt (E2.1).

    Every row states its type, source and verification level. In E2.1 the only
    verification level is ``unverified``: an observed provider response is stored as
    reported and is never authoritative confirmation.
    """

    __tablename__ = "payment_execution_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["attempt_id", "claim_id", "approval_id", "payload_hash"],
            [
                "payment_execution_attempts.attempt_id",
                "payment_execution_attempts.claim_id",
                "payment_execution_attempts.approval_id",
                "payment_execution_attempts.payload_hash",
            ],
            name="fk_payment_execution_evidence_attempt_identity",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("attempt_id", "sequence", name="uq_payment_execution_evidence_sequence"),
        UniqueConstraint(
            "attempt_id",
            "observation_key",
            name="uq_payment_execution_evidence_observation_key",
        ),
        CheckConstraint("sequence >= 1", name="ck_payment_execution_evidence_sequence"),
        CheckConstraint(
            "verification_level IN ('unverified')",
            name="ck_payment_execution_evidence_verification_level",
        ),
        CheckConstraint(
            "length(evidence_hash) = 64 AND evidence_hash = lower(evidence_hash)",
            name="ck_payment_execution_evidence_hash_format",
        ),
        CheckConstraint(
            "length(observation_key) > 0",
            name="ck_payment_execution_evidence_observation_key_not_empty",
        ),
        Index("ix_payment_execution_evidence_checkout_request_id", "checkout_request_id"),
    )

    evidence_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(String(36), nullable=False)
    claim_id: Mapped[str] = mapped_column(String(36), nullable=False)
    approval_id: Mapped[str] = mapped_column(String(36), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_source: Mapped[str] = mapped_column(String(32), nullable=False)
    verification_level: Mapped[str] = mapped_column(String(16), nullable=False)
    observation_key: Mapped[str] = mapped_column(String(200), nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    uncertainty_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    result_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    result_description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    checkout_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    merchant_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_transaction_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
