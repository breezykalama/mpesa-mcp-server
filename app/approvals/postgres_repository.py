"""PostgreSQL-backed durable approval repository.

Requests are created and read back (Slice D2), and verified human reviewers record
approve or reject decisions atomically with the resulting quorum transition (Slice D3).
Pending requests whose database deadline has passed are moved to ``expired`` atomically
(Slice D4.3). There is no generic update or delete operation. The repository is not
wired into the live approval service, and it never calls a payment provider: reaching
quorum only marks the request ``approved`` and expiry only marks it ``expired``.

The database is the single source of truth. ``ApprovalRequest.reviewer_ids`` and
``review_count`` are derived from the append-only ``approval_decisions`` rows rather
than stored separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast, get_args
from uuid import uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DataError, IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.approvals.integrity import (
    PAYLOAD_HASH_ALGORITHM,
    canonical_payload_hash,
    canonical_payload_json,
)
from app.approvals.models import ApprovalRequest, ApprovalStatus
from app.approvals.reviewer import VerifiedReviewer, normalize_trusted_issuers
from app.auth.oidc import OperatorIdentity
from app.infrastructure.health import InfrastructureUnavailableError
from app.storage.database import SessionFactory
from app.storage.models import ApprovalDecisionModel, ApprovalRequestModel

SUPPORTED_APPROVAL_STATUSES: frozenset[str] = frozenset(get_args(ApprovalStatus))
UNIQUE_VIOLATION_SQLSTATE = "23505"
STORAGE_UNAVAILABLE_MESSAGE = "Approval storage is unavailable."
DECISION_LOCK_TIMEOUT = "5s"
"""Maximum wait for the approval row lock before the decision fails as unavailable."""
DEFAULT_EXPIRY_BATCH_SIZE = 100
MAX_EXPIRY_BATCH_SIZE = 1000

DecisionValue = Literal["approve", "reject"]
DecisionOutcomeStatus = Literal["partially_approved", "approved", "rejected"]


class ApprovalPersistenceError(ValueError):
    """Raised when an approval write violates a persistence integrity rule."""


class DuplicateApprovalRequestError(ApprovalPersistenceError):
    """Raised when an approval request ID already exists. Existing rows are never replaced."""


class ApprovalRequestNotFoundError(LookupError):
    """Raised by operations that require an existing approval request."""


class ApprovalIntegrityError(RuntimeError):
    """Raised when a stored payment payload no longer matches its recorded hash."""


class ApprovalDecisionRejectedError(ValueError):
    """Base class for decisions refused because of the approval request's state."""


class ApprovalNotPendingError(ApprovalDecisionRejectedError):
    """Raised when the approval request is already approved, rejected or expired."""


class ApprovalExpiredError(ApprovalDecisionRejectedError):
    """Raised when a pending approval request has passed its expiry time."""


class DuplicateReviewerDecisionError(ApprovalDecisionRejectedError):
    """Raised when the reviewer (issuer + subject) already decided this approval."""


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


@dataclass(frozen=True)
class DecisionOutcome:
    """Result of an accepted reviewer decision, read inside the deciding transaction."""

    status: DecisionOutcomeStatus
    record: DurableApprovalRecord
    decision: ApprovalDecisionRecord


@dataclass(frozen=True)
class ExpiryOutcome:
    """Result of a targeted expiry attempt, read inside the expiring transaction.

    ``expired`` is true only for the call that performed the ``pending -> expired``
    transition; repeated or losing attempts return ``False`` with the persisted record.
    """

    expired: bool
    record: DurableApprovalRecord


class PostgresApprovalRepository:
    """Durable approval requests and reviewer decisions.

    Not-found behaviour:

    * ``get`` returns ``None`` for an unknown approval ID (matching the existing
      ``ApprovalRepositoryProtocol.get`` contract).
    * ``list_decisions`` raises ``ApprovalRequestNotFoundError`` for an unknown approval
      ID, so "no decisions yet" is distinguishable from "no such approval".

    Database failures raise ``InfrastructureUnavailableError``, matching the existing
    PostgreSQL transaction repository.
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        trusted_reviewer_issuers: frozenset[str] | set[str] | None = None,
    ) -> None:
        """Create the repository.

        ``trusted_reviewer_issuers`` lists the OIDC issuers whose verified tokens may
        record decisions (normally just ``OIDC_ISSUER``). It defaults to empty, so
        decisions are refused until issuers are configured explicitly (fail closed).
        """

        self._session_factory = session_factory
        self._trusted_reviewer_issuers = normalize_trusted_issuers(trusted_reviewer_issuers)

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

        # Keep the ID in a local: after commit the ORM instance may be expired and, once
        # the session closes, detached, so its attributes must not be read again.
        new_approval_id = approval_id or str(uuid4())
        model = ApprovalRequestModel(
            approval_id=new_approval_id,
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

        record = self.get(new_approval_id)
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

    def expire_request(self, approval_id: str) -> ExpiryOutcome:
        """Expire one pending request if its database deadline has passed (idempotent).

        One READ COMMITTED transaction runs a conditional ``UPDATE ... RETURNING`` whose
        target row is locked with ``SELECT ... FOR UPDATE``, the same row lock D3
        decisions take. A concurrent decision therefore either commits first (and the
        request is no longer ``pending``, so nothing is expired) or waits and then sees
        the request ``expired``. The deadline is checked with ``clock_timestamp()`` using
        the same boundary as decisions (expired once ``clock_timestamp() >= expires_at``).

        Approved, rejected, already-expired and not-yet-due requests are left unchanged
        and returned with ``expired=False``. Unknown IDs raise
        ``ApprovalRequestNotFoundError``. No decision row is written: expiry is never
        attributed to a reviewer.
        """

        try:
            with self._session_factory() as session, session.begin():
                _begin_locked_transaction(session)
                expired = _expire_due(session, approval_id=approval_id, limit=1, skip_locked=False)
                model = session.scalars(
                    select(ApprovalRequestModel)
                    .where(ApprovalRequestModel.approval_id == approval_id)
                    .execution_options(populate_existing=True)
                ).one_or_none()
                if model is None:
                    raise ApprovalRequestNotFoundError("Approval request not found.")
                record = _to_record(model, _load_decisions(session, approval_id))
                return ExpiryOutcome(expired=approval_id in expired, record=record)
        except (IntegrityError, DataError) as exc:
            raise ApprovalPersistenceError("Approval expiry violates a constraint.") from exc
        except SQLAlchemyError as exc:
            raise InfrastructureUnavailableError(STORAGE_UNAVAILABLE_MESSAGE) from exc

    def expire_due_requests(self, *, limit: int = DEFAULT_EXPIRY_BATCH_SIZE) -> tuple[str, ...]:
        """Expire up to ``limit`` pending requests past their deadline; return their IDs.

        Safe for concurrent workers: due rows are claimed with ``FOR UPDATE SKIP LOCKED``,
        so workers never block on each other or on an in-flight decision, never expire
        the same request twice, and only hold locks on the claimed batch for one short
        statement. A request skipped because a decision held its lock is picked up by a
        later run if it is still pending.
        """

        if isinstance(limit, bool) or not 1 <= limit <= MAX_EXPIRY_BATCH_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_EXPIRY_BATCH_SIZE}")
        try:
            with self._session_factory() as session, session.begin():
                _begin_locked_transaction(session)
                return _expire_due(session, approval_id=None, limit=limit, skip_locked=True)
        except (IntegrityError, DataError) as exc:
            raise ApprovalPersistenceError("Approval expiry violates a constraint.") from exc
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

    def record_decision(
        self,
        approval_id: str,
        *,
        reviewer: OperatorIdentity,
        decision: DecisionValue,
        comment: str | None = None,
        correlation_id: str | None = None,
    ) -> DecisionOutcome:
        """Atomically record one reviewer decision and apply the quorum transition.

        In a single READ COMMITTED transaction this:

        1. verifies the reviewer (verified human, approver role, trusted issuer);
        2. locks the approval row with ``SELECT ... FOR UPDATE`` so concurrent decisions
           on the same approval serialize in the database, across processes and workers;
        3. re-checks payload integrity, ``pending`` status and expiry (database clock);
        4. refuses a second decision by the same issuer + subject, including
           approve-then-reject;
        5. inserts the decision bound to the request's stored payload hash;
        6. derives the approval count from persisted decision rows, then advances
           ``version`` by exactly one and sets ``approved`` (quorum reached),
           ``rejected`` (any valid rejection is terminal), or leaves ``pending``.

        Any failure rolls the whole transaction back: no decision row without its state
        transition, and no transition without its decision. Refused decisions raise
        ``ReviewerNotEligibleError``, ``ApprovalRequestNotFoundError`` or an
        ``ApprovalDecisionRejectedError`` subclass and change nothing. No payment
        provider is called.
        """

        if decision not in ("approve", "reject"):
            raise ValueError(f"Unsupported decision: {decision!r}")
        verified = VerifiedReviewer.from_identity(
            reviewer,
            trusted_issuers=self._trusted_reviewer_issuers,
        )

        try:
            with self._session_factory() as session, session.begin():
                _begin_locked_transaction(session)
                return self._record_decision_locked(
                    session,
                    approval_id,
                    reviewer=verified,
                    decision=decision,
                    comment=comment,
                    correlation_id=correlation_id,
                )
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) == UNIQUE_VIOLATION_SQLSTATE:
                raise DuplicateReviewerDecisionError(
                    "Reviewer has already decided this approval request."
                ) from exc
            raise ApprovalPersistenceError(
                "Approval decision violates a persistence constraint."
            ) from exc
        except DataError as exc:
            raise ApprovalPersistenceError("Approval decision data is invalid.") from exc
        except SQLAlchemyError as exc:
            raise InfrastructureUnavailableError(STORAGE_UNAVAILABLE_MESSAGE) from exc

    def _record_decision_locked(
        self,
        session: Session,
        approval_id: str,
        *,
        reviewer: VerifiedReviewer,
        decision: DecisionValue,
        comment: str | None,
        correlation_id: str | None,
    ) -> DecisionOutcome:
        model = session.scalars(
            select(ApprovalRequestModel)
            .where(ApprovalRequestModel.approval_id == approval_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        if model is None:
            raise ApprovalRequestNotFoundError("Approval request not found.")

        _verify_payload_integrity(model)
        if model.status != "pending":
            raise ApprovalNotPendingError("Approval request is no longer pending.")
        # clock_timestamp(), not now(): now() is the transaction start time, which may be
        # well before this point if the transaction waited for the row lock.
        database_now = session.execute(select(func.clock_timestamp())).scalar_one()
        if database_now >= model.expires_at:
            raise ApprovalExpiredError("Approval request has expired.")

        # The row lock serializes every decision on this approval, and READ COMMITTED
        # gives this statement a snapshot that includes all previously committed ones.
        existing = _load_decisions(session, approval_id)
        if any(
            row.reviewer_issuer == reviewer.issuer and row.reviewer_subject == reviewer.subject
            for row in existing
        ):
            raise DuplicateReviewerDecisionError(
                "Reviewer has already decided this approval request."
            )

        decision_id = str(uuid4())
        session.add(
            ApprovalDecisionModel(
                decision_id=decision_id,
                approval_id=approval_id,
                payload_hash=model.payload_hash,
                decision=decision,
                reviewer_issuer=reviewer.issuer,
                reviewer_subject=reviewer.subject,
                reviewer_email=reviewer.email,
                reviewer_display_name=reviewer.display_name,
                reviewer_roles=list(reviewer.roles),
                authentication_method=reviewer.authentication_method,
                principal_type=reviewer.principal_type,
                comment=comment,
                correlation_id=correlation_id,
                decided_at=database_now,
            )
        )
        session.flush()

        approvals = sum(1 for row in existing if row.decision == "approve")
        if decision == "approve":
            approvals += 1
        status: DecisionOutcomeStatus
        new_values: dict[str, Any] = {"version": model.version + 1}
        if decision == "reject":
            status = "rejected"
            new_values.update(status="rejected", resolved_at=database_now)
        elif approvals >= model.required_reviewers:
            status = "approved"
            new_values.update(status="approved", resolved_at=database_now)
        else:
            status = "partially_approved"

        result = session.execute(
            update(ApprovalRequestModel)
            .where(
                ApprovalRequestModel.approval_id == approval_id,
                ApprovalRequestModel.version == model.version,
                ApprovalRequestModel.status == "pending",
            )
            .values(**new_values)
            .execution_options(synchronize_session=False)
        )
        if getattr(result, "rowcount", 0) != 1:  # pragma: no cover - guarded by row lock
            raise ApprovalPersistenceError("Approval request changed during the decision.")

        refreshed = session.scalars(
            select(ApprovalRequestModel)
            .where(ApprovalRequestModel.approval_id == approval_id)
            .execution_options(populate_existing=True)
        ).one()
        decisions = _load_decisions(session, approval_id)
        record = _to_record(refreshed, decisions)
        recorded = next(row for row in decisions if row.decision_id == decision_id)
        return DecisionOutcome(status=status, record=record, decision=recorded)


def _begin_locked_transaction(session: Session) -> None:
    """Pin READ COMMITTED and a bounded lock wait for a decision or expiry transaction.

    After waiting on a row lock, each statement must see the changes committed by the
    previous lock holder.
    """

    session.connection(execution_options={"isolation_level": "READ COMMITTED"})
    session.execute(text(f"SET LOCAL lock_timeout = '{DECISION_LOCK_TIMEOUT}'"))


def _expire_due(
    session: Session,
    *,
    approval_id: str | None,
    limit: int,
    skip_locked: bool,
) -> tuple[str, ...]:
    """Atomically move due ``pending`` rows to ``expired``; return the expired IDs.

    The subquery locks the due rows (``FOR UPDATE``). In READ COMMITTED, a row locked by
    a concurrent decision is re-checked after that lock is released, so a request that
    became ``approved`` or ``rejected`` no longer qualifies. The outer
    ``status = 'pending'`` guard and the database trigger (resolved rows are immutable;
    version advances by exactly one) make a second transition impossible.
    """

    database_now = func.clock_timestamp()
    due = (
        select(ApprovalRequestModel.approval_id)
        .where(
            ApprovalRequestModel.status == "pending",
            ApprovalRequestModel.expires_at <= database_now,
        )
        .order_by(ApprovalRequestModel.expires_at, ApprovalRequestModel.approval_id)
        .limit(limit)
        .with_for_update(skip_locked=skip_locked)
    )
    if approval_id is not None:
        due = due.where(ApprovalRequestModel.approval_id == approval_id)
    # MATERIALIZED evaluates the locking subquery exactly once. As a plain ``IN``
    # subquery, a nested-loop plan may rescan it per outer row; each rescan skips rows
    # this statement already updated and claims the next ones, ignoring ``limit``.
    claimed = due.cte("due_approval_requests").prefix_with("MATERIALIZED")
    result = session.execute(
        update(ApprovalRequestModel)
        .where(
            ApprovalRequestModel.approval_id.in_(select(claimed.c.approval_id)),
            ApprovalRequestModel.status == "pending",
        )
        .values(
            status="expired",
            resolved_at=database_now,
            version=ApprovalRequestModel.version + 1,
        )
        .returning(ApprovalRequestModel.approval_id)
        .execution_options(synchronize_session=False)
    )
    return tuple(result.scalars().all())


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


def _verify_payload_integrity(model: ApprovalRequestModel) -> None:
    if (
        model.payload_hash_algorithm != PAYLOAD_HASH_ALGORITHM
        or canonical_payload_hash(model.payment_payload) != model.payload_hash
    ):
        raise ApprovalIntegrityError("Stored payment payload does not match its hash.")


def _to_record(
    model: ApprovalRequestModel,
    decisions: tuple[ApprovalDecisionRecord, ...],
) -> DurableApprovalRecord:
    _verify_payload_integrity(model)

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
