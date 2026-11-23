"""PostgreSQL-backed durable approval repository: create and read operations.

Slice D2 scope: requests are created and read back, and reviewer decisions are read as
immutable records. Recording decisions, quorum transitions and expiry belong to later
slices, so this repository intentionally exposes no update or delete operation. It is
not yet wired into the approval service or payment execution.

The database is the single source of truth. ``ApprovalRequest.reviewer_ids`` and
``review_count`` are derived from the append-only ``approval_decisions`` rows rather
than stored separately.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast, get_args
from uuid import uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.approvals.integrity import (
    PAYLOAD_HASH_ALGORITHM,
    canonical_payload_hash,
    canonical_payload_json,
)
from app.approvals.models import ApprovalRequest, ApprovalStatus
from app.infrastructure.health import InfrastructureUnavailableError
from app.storage.database import SessionFactory
from app.storage.models import ApprovalDecisionModel, ApprovalRequestModel

SUPPORTED_APPROVAL_STATUSES: frozenset[str] = frozenset(get_args(ApprovalStatus))
UNIQUE_VIOLATION_SQLSTATE = "23505"
STORAGE_UNAVAILABLE_MESSAGE = "Approval storage is unavailable."


class ApprovalPersistenceError(ValueError):
    """Raised when an approval write violates a persistence integrity rule."""


class DuplicateApprovalRequestError(ApprovalPersistenceError):
    """Raised when an approval request ID already exists. Existing rows are never replaced."""


class ApprovalRequestNotFoundError(LookupError):
    """Raised by operations that require an existing approval request."""


class ApprovalIntegrityError(RuntimeError):
    """Raised when a stored payment payload no longer matches its recorded hash."""


class ApprovalDecisionRecord(BaseModel):
    """Read-only view of one persisted reviewer decision."""

    model_config = ConfigDict(frozen=True)

    decision_id: str
    approval_id: str
    payload_hash: str
    decision: str
    reviewer_issuer: str
    reviewer_subject: str
    reviewer_email: str | None
    reviewer_display_name: str | None
    reviewer_roles: tuple[str, ...]
    authentication_method: str
    principal_type: str
    comment: str | None
    correlation_id: str | None
    decided_at: datetime


class DurableApprovalRecord(BaseModel):
    """Persisted approval request: the domain model plus its durable snapshots.

    The existing ``ApprovalRequest`` domain model is preserved unchanged. Fields it
    cannot express (hash, policy snapshot, attribution, version) are carried alongside
    it rather than added to the domain model.
    """

    model_config = ConfigDict(frozen=True)

    approval: ApprovalRequest
    payload_hash_algorithm: str
    payload_hash: str
    policy_version: str
    policy_snapshot: dict[str, Any]
    requested_by: str | None
    correlation_id: str | None
    resolved_at: datetime | None
    version: int
    decisions: tuple[ApprovalDecisionRecord, ...]


class PostgresApprovalRepository:
    """Create and read durable approval requests.

    Not-found behaviour:

    * ``get`` returns ``None`` for an unknown approval ID (matching the existing
      ``ApprovalRepositoryProtocol.get`` contract).
    * ``list_decisions`` raises ``ApprovalRequestNotFoundError`` for an unknown approval
      ID, so "no decisions yet" is distinguishable from "no such approval".

    Database failures raise ``InfrastructureUnavailableError``, matching the existing
    PostgreSQL transaction repository.
    """

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def create(
        self,
        *,
        action: str,
        payload: dict[str, Any],
        reason: str,
        expires_at: datetime,
        required_reviewers: int,
        policy_version: str,
        policy_snapshot: dict[str, Any],
        requested_by: str | None = None,
        correlation_id: str | None = None,
        approval_id: str | None = None,
        created_at: datetime | None = None,
    ) -> DurableApprovalRecord:
        """Insert a new pending approval request and return the committed record.

        This is insert-only: an existing ``approval_id`` raises
        ``DuplicateApprovalRequestError`` and the stored row is left untouched. On any
        failure the transaction is rolled back and nothing is persisted.
        """

        created = _require_aware_utc(created_at or datetime.now(UTC), "created_at")
        expires = _require_aware_utc(expires_at, "expires_at")
        canonical_payload_json(payload)
        canonical_payload_json(policy_snapshot)

        model = ApprovalRequestModel(
            approval_id=approval_id or str(uuid4()),
            action=action,
            status="pending",
            reason=reason,
            payment_payload=payload,
            payload_hash_algorithm=PAYLOAD_HASH_ALGORITHM,
            payload_hash=canonical_payload_hash(payload),
            policy_version=policy_version,
            policy_snapshot=policy_snapshot,
            required_reviewers=required_reviewers,
            requested_by=requested_by,
            correlation_id=correlation_id,
            created_at=created,
            expires_at=expires,
        )

        with self._session_factory() as session:
            try:
                session.add(model)
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                if getattr(exc.orig, "sqlstate", None) == UNIQUE_VIOLATION_SQLSTATE:
                    raise DuplicateApprovalRequestError(
                        "Approval request ID already exists."
                    ) from exc
                raise ApprovalPersistenceError(
                    "Approval request violates a persistence constraint."
                ) from exc
            except SQLAlchemyError as exc:
                session.rollback()
                raise InfrastructureUnavailableError(STORAGE_UNAVAILABLE_MESSAGE) from exc

        record = self.get(model.approval_id)
        if record is None:  # pragma: no cover - committed row must be readable
            raise InfrastructureUnavailableError(STORAGE_UNAVAILABLE_MESSAGE)
        return record

    def get(self, approval_id: str) -> DurableApprovalRecord | None:
        """Return the approval request and its decisions, or ``None`` if unknown."""

        try:
            with self._session_factory() as session:
                model = session.get(ApprovalRequestModel, approval_id)
                if model is None:
                    return None
                return _to_record(model, _load_decisions(session, approval_id))
        except SQLAlchemyError as exc:
            raise InfrastructureUnavailableError(STORAGE_UNAVAILABLE_MESSAGE) from exc

    def list_by_status(self, status: ApprovalStatus) -> list[DurableApprovalRecord]:
        """Return approval requests with ``status``, oldest first."""

        if status not in SUPPORTED_APPROVAL_STATUSES:
            raise ValueError(f"Unsupported approval status: {status!r}")
        try:
            with self._session_factory() as session:
                models = session.scalars(
                    select(ApprovalRequestModel)
                    .where(ApprovalRequestModel.status == status)
                    .order_by(ApprovalRequestModel.created_at, ApprovalRequestModel.approval_id)
                ).all()
                return [
                    _to_record(model, _load_decisions(session, model.approval_id))
                    for model in models
                ]
        except SQLAlchemyError as exc:
            raise InfrastructureUnavailableError(STORAGE_UNAVAILABLE_MESSAGE) from exc

    def list_decisions(self, approval_id: str) -> tuple[ApprovalDecisionRecord, ...]:
        """Return the reviewer decisions for an approval, oldest first."""

        try:
            with self._session_factory() as session:
                if session.get(ApprovalRequestModel, approval_id) is None:
                    raise ApprovalRequestNotFoundError("Approval request not found.")
                return _load_decisions(session, approval_id)
        except SQLAlchemyError as exc:
            raise InfrastructureUnavailableError(STORAGE_UNAVAILABLE_MESSAGE) from exc


def _require_aware_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _load_decisions(session: Session, approval_id: str) -> tuple[ApprovalDecisionRecord, ...]:
    models = session.scalars(
        select(ApprovalDecisionModel)
        .where(ApprovalDecisionModel.approval_id == approval_id)
        .order_by(ApprovalDecisionModel.decided_at, ApprovalDecisionModel.decision_id)
    ).all()
    return tuple(
        ApprovalDecisionRecord(
            decision_id=model.decision_id,
            approval_id=model.approval_id,
            payload_hash=model.payload_hash,
            decision=model.decision,
            reviewer_issuer=model.reviewer_issuer,
            reviewer_subject=model.reviewer_subject,
            reviewer_email=model.reviewer_email,
            reviewer_display_name=model.reviewer_display_name,
            reviewer_roles=tuple(model.reviewer_roles),
            authentication_method=model.authentication_method,
            principal_type=model.principal_type,
            comment=model.comment,
            correlation_id=model.correlation_id,
            decided_at=model.decided_at.astimezone(UTC),
        )
        for model in models
    )


def _to_record(
    model: ApprovalRequestModel,
    decisions: tuple[ApprovalDecisionRecord, ...],
) -> DurableApprovalRecord:
    if (
        model.payload_hash_algorithm != PAYLOAD_HASH_ALGORITHM
        or canonical_payload_hash(model.payment_payload) != model.payload_hash
    ):
        raise ApprovalIntegrityError("Stored payment payload does not match its hash.")

    if model.status not in SUPPORTED_APPROVAL_STATUSES:
        raise ApprovalIntegrityError("Stored approval status is not supported.")
    status = cast(ApprovalStatus, model.status)
    resolved_at = model.resolved_at.astimezone(UTC) if model.resolved_at else None
    approvals = [decision for decision in decisions if decision.decision == "approve"]
    approval = ApprovalRequest(
        approval_id=model.approval_id,
        action=model.action,
        payload=model.payment_payload,
        reason=model.reason,
        status=status,
        created_at=model.created_at.astimezone(UTC),
        expires_at=model.expires_at.astimezone(UTC),
        reviewed_at=(
            resolved_at
            if status in ("approved", "rejected")
            else (decisions[-1].decided_at if decisions else None)
        ),
        expired_at=resolved_at if status == "expired" else None,
        required_reviewers=model.required_reviewers,
        reviewer_ids=[decision.reviewer_subject for decision in approvals],
        review_count=len(approvals),
    )
    return DurableApprovalRecord(
        approval=approval,
        payload_hash_algorithm=model.payload_hash_algorithm,
        payload_hash=model.payload_hash,
        policy_version=model.policy_version,
        policy_snapshot=model.policy_snapshot,
        resolved_at=resolved_at,
        requested_by=model.requested_by,
        correlation_id=model.correlation_id,
        version=model.version,
        decisions=decisions,
    )
