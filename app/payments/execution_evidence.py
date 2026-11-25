"""Durable payment execution attempts and append-only outcome evidence (Slice E2.1).

This module records *what was observed* about executing a claimed payment. It never
executes, retries or confirms a payment, and it is not exposed through MCP tools, HTTP
routes or the live payment service.

Model, per E1 execution claim:

* **Claim acquired**: the E1 ``payment_execution_claims`` row (unchanged here).
* **Attempt recorded**: at most one ``payment_execution_attempts`` row, created by the
  claim owner, who proves ownership with the E1 claim token.
* **Submission started**: ``submission_started`` evidence, written by the owner
  *before* calling the provider. It means the payment may have been sent, not that the
  provider received it.
* **Outcome unknown**: ``outcome_unknown`` evidence (timeout, connection error, ...).
* **Provider response observed**: ``provider_response_observed`` evidence from the
  initiation response, a callback or a status query, stored exactly as reported.
* **Verified terminal outcome**: *not recordable in E2.1.* Every row is stored with
  ``verification_level = 'unverified'`` (enforced by a database CHECK) and callers
  cannot choose a verification level. A trusted provider-verification path is E2.2.

Uncertainty is conservative: ``assess_attempt`` never reports a confirmed outcome and
never reports that resubmission is safe, and the absence of evidence is never treated
as proof that no provider side effect occurred.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from secrets import compare_digest
from typing import Literal, TypeVar, get_args
from uuid import uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.exc import DataError, IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.approvals.integrity import canonical_payload_hash
from app.approvals.postgres_repository import (
    STORAGE_UNAVAILABLE_MESSAGE,
    UNIQUE_VIOLATION_SQLSTATE,
    ApprovalIntegrityError,
    _begin_locked_transaction,
    _verify_payload_integrity,
)
from app.infrastructure.health import InfrastructureUnavailableError
from app.payments.execution_claims import claim_token_hash
from app.storage.database import SessionFactory
from app.storage.models import (
    ApprovalRequestModel,
    PaymentExecutionAttemptModel,
    PaymentExecutionClaimModel,
    PaymentExecutionEvidenceModel,
)

EvidenceType = Literal["submission_started", "outcome_unknown", "provider_response_observed"]
EvidenceSource = Literal[
    "execution_worker",
    "provider_initiation_response",
    "provider_callback",
    "provider_status_query",
]
ProviderObservationSource = Literal[
    "provider_initiation_response",
    "provider_callback",
    "provider_status_query",
]
UncertaintyReason = Literal["timeout", "connection_error", "unexpected_error", "worker_interrupted"]
VerificationLevel = Literal["unverified"]
AttemptStage = Literal[
    "no_attempt",
    "attempt_recorded",
    "submission_possible",
    "outcome_unknown",
    "provider_response_observed",
    "conflicting_evidence",
]

PROVIDER_OBSERVATION_SOURCES: frozenset[str] = frozenset(get_args(ProviderObservationSource))
UNCERTAINTY_REASONS: frozenset[str] = frozenset(get_args(UncertaintyReason))
UNVERIFIED: VerificationLevel = "unverified"
DEFAULT_PROVIDER = "daraja"
DEFAULT_RAIL = "mpesa"
MAX_OBSERVATION_KEY_LENGTH = 160
CORRELATION_FIELDS = ("checkout_request_id", "merchant_request_id", "provider_transaction_id")
_T = TypeVar("_T")


class ExecutionEvidenceError(ValueError):
    """Base class for refused execution-evidence writes."""


class ExecutionClaimNotFoundError(LookupError):
    """Raised when the execution claim does not exist."""


class ExecutionAttemptNotFoundError(LookupError):
    """Raised when the execution attempt does not exist."""


class ClaimOwnershipError(PermissionError):
    """Raised when the presented claim token does not belong to the claim."""


class AttemptAlreadyRecordedError(ExecutionEvidenceError):
    """Raised when the claim already has an execution attempt.

    A second attempt is never created automatically: an existing attempt may already
    have reached the provider, so its evidence must be reconciled instead.
    """


class SubmissionNotStartedError(ExecutionEvidenceError):
    """Raised when evidence other than ``submission_started`` precedes it."""


class ConflictingEvidenceError(ExecutionEvidenceError):
    """Raised when an observation key is reused with different content."""


class InvalidEvidenceError(ExecutionEvidenceError):
    """Raised when evidence fields are missing, malformed or out of bounds."""


class ExecutionAttemptRecord(BaseModel):
    """Read-only view of a persisted execution attempt (never includes the claim token)."""

    model_config = ConfigDict(frozen=True)

    attempt_id: str
    claim_id: str
    approval_id: str
    payload_hash: str
    provider: str
    rail: str
    idempotency_key: str | None
    recorded_by: str
    correlation_id: str | None
    recorded_at: datetime


class ExecutionEvidenceRecord(BaseModel):
    """Read-only view of one append-only evidence row."""

    model_config = ConfigDict(frozen=True)

    evidence_id: str
    attempt_id: str
    claim_id: str
    approval_id: str
    payload_hash: str
    sequence: int
    evidence_type: EvidenceType
    evidence_source: EvidenceSource
    verification_level: VerificationLevel
    observation_key: str
    uncertainty_reason: str | None
    result_code: str | None
    result_description: str | None
    checkout_request_id: str | None
    merchant_request_id: str | None
    provider_transaction_id: str | None
    recorded_at: datetime


@dataclass(frozen=True)
class EvidenceOutcome:
    """Result of an evidence write. ``created`` is false for an identical duplicate."""

    created: bool
    evidence: ExecutionEvidenceRecord


@dataclass(frozen=True)
class AttemptAssessment:
    """Conservative reading of an attempt's evidence.

    ``confirmed`` and ``resubmission_safe`` are always ``False`` in E2.1: unverified
    observations are never authoritative, and once a claim exists a provider side effect
    can never be ruled out from missing evidence.
    """

    stage: AttemptStage
    observed_result_codes: tuple[str, ...]
    conflicting: bool
    confirmed: Literal[False] = False
    resubmission_safe: Literal[False] = False


class PostgresExecutionEvidenceRepository:
    """Record and read execution attempts and evidence for E1 claims.

    Internal infrastructure only: nothing in the application container builds it.
    Database failures raise ``InfrastructureUnavailableError``; there is no in-memory
    fallback.
    """

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    # --- Writes -------------------------------------------------------------------------

    def record_attempt(
        self,
        claim_id: str,
        *,
        claim_token: str,
        correlation_id: str | None = None,
    ) -> ExecutionAttemptRecord:
        """Record the single execution attempt for a claim, as the claim owner.

        Locks the claim row ``FOR UPDATE``, verifies the claim token against its stored
        hash, re-verifies the approval payload integrity, and refuses a second attempt.
        The idempotency key is read from the stored approval payload, never from the
        caller. ``recorded_by`` is the claim owner from the database.
        """

        return self._write(
            lambda session: self._record_attempt(
                session, claim_id, claim_token=claim_token, correlation_id=correlation_id
            )
        )

    def record_submission_started(self, attempt_id: str, *, claim_token: str) -> EvidenceOutcome:
        """Record, before calling the provider, that the payment may now be sent."""

        return self._write(
            lambda session: self._append(
                session,
                attempt_id,
                claim_token=claim_token,
                evidence_type="submission_started",
                evidence_source="execution_worker",
                observation_key="submission_started",
                fields={},
            )
        )

    def record_outcome_unknown(
        self, attempt_id: str, *, claim_token: str, reason: UncertaintyReason
    ) -> EvidenceOutcome:
        """Record that the owner could not determine what the provider did."""

        if reason not in UNCERTAINTY_REASONS:
            raise InvalidEvidenceError(f"Unsupported uncertainty reason: {reason!r}")
        return self._write(
            lambda session: self._append(
                session,
                attempt_id,
                claim_token=claim_token,
                evidence_type="outcome_unknown",
                evidence_source="execution_worker",
                observation_key="outcome_unknown",
                fields={"uncertainty_reason": reason},
            )
        )

    def record_provider_observation(
        self,
        attempt_id: str,
        *,
        source: ProviderObservationSource,
        observation_key: str,
        result_code: str,
        result_description: str | None = None,
        checkout_request_id: str | None = None,
        merchant_request_id: str | None = None,
        provider_transaction_id: str | None = None,
        claim_token: str | None = None,
    ) -> EvidenceOutcome:
        """Record a provider response exactly as observed, always ``unverified``.

        ``observation_key`` is the caller's stable identifier for this observation
        (for example a callback replay key); replaying it with identical content is
        idempotent and with different content raises ``ConflictingEvidenceError``.
        The initiation response is seen only by the claim owner, so it requires the
        claim token; callbacks and status queries do not carry one and must not pass it.
        No argument can make an observation authoritative.
        """

        if source not in PROVIDER_OBSERVATION_SOURCES:
            raise InvalidEvidenceError(f"Unsupported observation source: {source!r}")
        if (source == "provider_initiation_response") != (claim_token is not None):
            raise InvalidEvidenceError(
                "Only the provider initiation response is recorded with the claim token."
            )
        key = _bounded("observation_key", observation_key, MAX_OBSERVATION_KEY_LENGTH)
        fields = {
            "result_code": _bounded("result_code", result_code, 32),
            "result_description": _optional_bounded("result_description", result_description, 255),
            "checkout_request_id": _optional_bounded(
                "checkout_request_id", checkout_request_id, 128
            ),
            "merchant_request_id": _optional_bounded(
                "merchant_request_id", merchant_request_id, 128
            ),
            "provider_transaction_id": _optional_bounded(
                "provider_transaction_id", provider_transaction_id, 128
            ),
        }
        return self._write(
            lambda session: self._append(
                session,
                attempt_id,
                claim_token=claim_token,
                evidence_type="provider_response_observed",
                evidence_source=source,
                observation_key=f"{source}:{key}",
                fields=fields,
            )
        )

    # --- Reads --------------------------------------------------------------------------

    def get_attempt(self, claim_id: str) -> ExecutionAttemptRecord | None:
        """Return the claim's attempt, or ``None`` if none was recorded."""

        try:
            with self._session_factory() as session:
                model = _load_attempt_for_claim(session, claim_id)
                return None if model is None else _to_attempt_record(model)
        except SQLAlchemyError as exc:
            raise InfrastructureUnavailableError(STORAGE_UNAVAILABLE_MESSAGE) from exc

    def list_evidence(self, claim_id: str) -> tuple[ExecutionEvidenceRecord, ...]:
        """Return the claim's evidence in recording order (``sequence``)."""

        try:
            with self._session_factory() as session:
                if session.get(PaymentExecutionClaimModel, claim_id) is None:
                    raise ExecutionClaimNotFoundError("Execution claim not found.")
                return _load_evidence(session, claim_id)
        except SQLAlchemyError as exc:
            raise InfrastructureUnavailableError(STORAGE_UNAVAILABLE_MESSAGE) from exc

    def assess(self, claim_id: str) -> AttemptAssessment:
        """Return the conservative assessment of the claim's persisted evidence."""

        try:
            with self._session_factory() as session, session.begin():
                # One snapshot for both reads.
                session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
                if session.get(PaymentExecutionClaimModel, claim_id) is None:
                    raise ExecutionClaimNotFoundError("Execution claim not found.")
                attempt = _load_attempt_for_claim(session, claim_id)
                evidence = _load_evidence(session, claim_id)
        except SQLAlchemyError as exc:
            raise InfrastructureUnavailableError(STORAGE_UNAVAILABLE_MESSAGE) from exc
        return assess_attempt(attempt_recorded=attempt is not None, evidence=evidence)

    # --- Internals ----------------------------------------------------------------------

    def _write(self, operation: Callable[[Session], _T]) -> _T:
        try:
            with self._session_factory() as session, session.begin():
                _begin_locked_transaction(session)
                return operation(session)
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) == UNIQUE_VIOLATION_SQLSTATE:
                raise ConflictingEvidenceError(
                    "Execution evidence conflicts with an existing record."
                ) from exc
            raise InvalidEvidenceError("Execution evidence violates a constraint.") from exc
        except DataError as exc:
            raise InvalidEvidenceError("Execution evidence data is invalid.") from exc
        except SQLAlchemyError as exc:
            raise InfrastructureUnavailableError(STORAGE_UNAVAILABLE_MESSAGE) from exc

    def _record_attempt(
        self,
        session: Session,
        claim_id: str,
        *,
        claim_token: str,
        correlation_id: str | None,
    ) -> ExecutionAttemptRecord:
        claim = session.scalars(
            select(PaymentExecutionClaimModel)
            .where(PaymentExecutionClaimModel.claim_id == claim_id)
            .with_for_update()
        ).one_or_none()
        if claim is None:
            raise ExecutionClaimNotFoundError("Execution claim not found.")
        _verify_claim_token(claim, claim_token)

        approval = session.get(ApprovalRequestModel, claim.approval_id)
        if approval is None or approval.payload_hash != claim.payload_hash:
            raise ApprovalIntegrityError("Claim is not bound to its approval payload.")
        _verify_payload_integrity(approval)

        if _load_attempt_for_claim(session, claim_id) is not None:
            raise AttemptAlreadyRecordedError("Execution claim already has an attempt.")

        idempotency_key = approval.payment_payload.get("idempotency_key")
        attempt_id = str(uuid4())
        session.add(
            PaymentExecutionAttemptModel(
                attempt_id=attempt_id,
                claim_id=claim.claim_id,
                approval_id=claim.approval_id,
                payload_hash=claim.payload_hash,
                provider=DEFAULT_PROVIDER,
                rail=DEFAULT_RAIL,
                idempotency_key=idempotency_key if isinstance(idempotency_key, str) else None,
                recorded_by=claim.claimed_by,
                correlation_id=correlation_id,
                recorded_at=func.clock_timestamp(),
            )
        )
        session.flush()
        model = session.scalars(
            select(PaymentExecutionAttemptModel)
            .where(PaymentExecutionAttemptModel.attempt_id == attempt_id)
            .execution_options(populate_existing=True)
        ).one()
        return _to_attempt_record(model)

    def _append(
        self,
        session: Session,
        attempt_id: str,
        *,
        claim_token: str | None,
        evidence_type: EvidenceType,
        evidence_source: EvidenceSource,
        observation_key: str,
        fields: dict[str, str | None],
    ) -> EvidenceOutcome:
        # The attempt row lock serializes every evidence write for this attempt.
        attempt = session.scalars(
            select(PaymentExecutionAttemptModel)
            .where(PaymentExecutionAttemptModel.attempt_id == attempt_id)
            .with_for_update()
        ).one_or_none()
        if attempt is None:
            raise ExecutionAttemptNotFoundError("Execution attempt not found.")
        if claim_token is not None:
            claim = session.get(PaymentExecutionClaimModel, attempt.claim_id)
            if claim is None:  # pragma: no cover - guaranteed by the foreign key
                raise ExecutionClaimNotFoundError("Execution claim not found.")
            _verify_claim_token(claim, claim_token)

        content = {
            "evidence_type": evidence_type,
            "evidence_source": evidence_source,
            "verification_level": UNVERIFIED,
            **{name: fields.get(name) for name in _CONTENT_FIELDS},
        }
        evidence_hash = canonical_payload_hash(content)

        existing = session.scalars(
            select(PaymentExecutionEvidenceModel).where(
                PaymentExecutionEvidenceModel.attempt_id == attempt_id,
                PaymentExecutionEvidenceModel.observation_key == observation_key,
            )
        ).one_or_none()
        if existing is not None:
            if existing.evidence_hash != evidence_hash:
                raise ConflictingEvidenceError(
                    "Observation key was already recorded with different content."
                )
            return EvidenceOutcome(created=False, evidence=_to_evidence_record(existing))

        if evidence_type != "submission_started" and not _has_submission(session, attempt_id):
            raise SubmissionNotStartedError("Attempt has no submission_started evidence.")

        sequence = (
            session.execute(
                select(func.coalesce(func.max(PaymentExecutionEvidenceModel.sequence), 0)).where(
                    PaymentExecutionEvidenceModel.attempt_id == attempt_id
                )
            ).scalar_one()
            + 1
        )
        evidence_id = str(uuid4())
        session.add(
            PaymentExecutionEvidenceModel(
                evidence_id=evidence_id,
                attempt_id=attempt.attempt_id,
                claim_id=attempt.claim_id,
                approval_id=attempt.approval_id,
                payload_hash=attempt.payload_hash,
                sequence=sequence,
                evidence_type=evidence_type,
                evidence_source=evidence_source,
                verification_level=UNVERIFIED,
                observation_key=observation_key,
                evidence_hash=evidence_hash,
                recorded_at=func.clock_timestamp(),
                **{name: fields.get(name) for name in _CONTENT_FIELDS},
            )
        )
        session.flush()
        model = session.scalars(
            select(PaymentExecutionEvidenceModel)
            .where(PaymentExecutionEvidenceModel.evidence_id == evidence_id)
            .execution_options(populate_existing=True)
        ).one()
        return EvidenceOutcome(created=True, evidence=_to_evidence_record(model))


_CONTENT_FIELDS = (
    "uncertainty_reason",
    "result_code",
    "result_description",
    *CORRELATION_FIELDS,
)


def assess_attempt(
    *,
    attempt_recorded: bool,
    evidence: tuple[ExecutionEvidenceRecord, ...],
) -> AttemptAssessment:
    """Derive the conservative stage of an attempt from its evidence.

    Observations are compared, never ranked: if provider observations disagree on the
    result code or on any correlation identifier, the stage is
    ``conflicting_evidence``. No stage implies confirmation or safe resubmission.
    """

    observations = [row for row in evidence if row.evidence_type == "provider_response_observed"]
    codes = tuple(dict.fromkeys(row.result_code for row in observations if row.result_code))
    conflicting = len(codes) > 1 or any(
        len({value for row in observations if (value := getattr(row, name)) is not None}) > 1
        for name in CORRELATION_FIELDS
    )

    stage: AttemptStage
    if conflicting:
        stage = "conflicting_evidence"
    elif observations:
        stage = "provider_response_observed"
    elif any(row.evidence_type == "outcome_unknown" for row in evidence):
        stage = "outcome_unknown"
    elif any(row.evidence_type == "submission_started" for row in evidence):
        stage = "submission_possible"
    elif attempt_recorded:
        stage = "attempt_recorded"
    else:
        stage = "no_attempt"
    return AttemptAssessment(stage=stage, observed_result_codes=codes, conflicting=conflicting)


def _verify_claim_token(claim: PaymentExecutionClaimModel, claim_token: str) -> None:
    if not isinstance(claim_token, str) or not compare_digest(
        claim_token_hash(claim_token), claim.claim_token_hash
    ):
        raise ClaimOwnershipError("Claim token does not match the execution claim.")


def _bounded(name: str, value: object, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise InvalidEvidenceError(f"{name} must be a non-empty, trimmed string")
    if len(value) > limit or "\x00" in value:
        raise InvalidEvidenceError(f"{name} must be at most {limit} characters")
    return value


def _optional_bounded(name: str, value: object, limit: int) -> str | None:
    return None if value is None else _bounded(name, value, limit)


def _has_submission(session: Session, attempt_id: str) -> bool:
    return (
        session.scalars(
            select(PaymentExecutionEvidenceModel.evidence_id).where(
                PaymentExecutionEvidenceModel.attempt_id == attempt_id,
                PaymentExecutionEvidenceModel.evidence_type == "submission_started",
            )
        ).first()
        is not None
    )


def _load_attempt_for_claim(session: Session, claim_id: str) -> PaymentExecutionAttemptModel | None:
    return session.scalars(
        select(PaymentExecutionAttemptModel).where(
            PaymentExecutionAttemptModel.claim_id == claim_id
        )
    ).one_or_none()


def _load_evidence(session: Session, claim_id: str) -> tuple[ExecutionEvidenceRecord, ...]:
    models = session.scalars(
        select(PaymentExecutionEvidenceModel)
        .where(PaymentExecutionEvidenceModel.claim_id == claim_id)
        .order_by(PaymentExecutionEvidenceModel.attempt_id, PaymentExecutionEvidenceModel.sequence)
    ).all()
    return tuple(_to_evidence_record(model) for model in models)


def _to_attempt_record(model: PaymentExecutionAttemptModel) -> ExecutionAttemptRecord:
    return ExecutionAttemptRecord(
        attempt_id=model.attempt_id,
        claim_id=model.claim_id,
        approval_id=model.approval_id,
        payload_hash=model.payload_hash,
        provider=model.provider,
        rail=model.rail,
        idempotency_key=model.idempotency_key,
        recorded_by=model.recorded_by,
        correlation_id=model.correlation_id,
        recorded_at=model.recorded_at.astimezone(UTC),
    )


def _to_evidence_record(model: PaymentExecutionEvidenceModel) -> ExecutionEvidenceRecord:
    return ExecutionEvidenceRecord.model_validate(
        {
            "evidence_id": model.evidence_id,
            "attempt_id": model.attempt_id,
            "claim_id": model.claim_id,
            "approval_id": model.approval_id,
            "payload_hash": model.payload_hash,
            "sequence": model.sequence,
            "evidence_type": model.evidence_type,
            "evidence_source": model.evidence_source,
            "verification_level": model.verification_level,
            "observation_key": model.observation_key,
            "uncertainty_reason": model.uncertainty_reason,
            "result_code": model.result_code,
            "result_description": model.result_description,
            "checkout_request_id": model.checkout_request_id,
            "merchant_request_id": model.merchant_request_id,
            "provider_transaction_id": model.provider_transaction_id,
            "recorded_at": model.recorded_at.astimezone(UTC),
        }
    )
