"""PostgreSQL tests for durable payment execution attempts and evidence (Slice E2.1).

Attempts are recorded once per E1 claim by the claim owner (proved with the claim
token); evidence is append-only, ordered per attempt, deduplicated by observation key
and content hash, and always ``unverified``. These tests prove evidence stays bound to
its claim, duplicates and conflicts are never silently merged, concurrent writers cannot
corrupt an attempt, uncertainty is never read as success, failures leave nothing
partial, and nothing reaches a payment provider or an external interface.

Concurrency tests run every worker on its own engine (independent connections and
pools). Fixtures live in ``tests/integration/conftest.py``; these tests skip unless
``MPESA_TEST_POSTGRES_URL`` points at a local disposable ``*_test`` database.
"""

from __future__ import annotations

import ast
import inspect as pyinspect
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from datetime import timedelta
from functools import partial
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from app.approvals.postgres_repository import PostgresApprovalRepository
from app.bootstrap.container import AppContainer
from app.config import get_settings
from app.daraja.client import MockDarajaClient
from app.infrastructure.health import InfrastructureUnavailableError
from app.main import app
from app.payments import execution_evidence
from app.payments.execution_claims import (
    ClaimOutcome,
    ExecutionAlreadyClaimedError,
    PostgresExecutionClaimRepository,
)
from app.payments.execution_evidence import (
    AttemptAlreadyRecordedError,
    ClaimOwnershipError,
    ConflictingEvidenceError,
    EvidenceOutcome,
    ExecutionAttemptNotFoundError,
    ExecutionAttemptRecord,
    ExecutionClaimNotFoundError,
    ExecutionEvidenceRecord,
    InvalidEvidenceError,
    PostgresExecutionEvidenceRepository,
    SubmissionNotStartedError,
)
from app.payments.providers import DarajaPaymentProvider
from app.policy.tool_policy import AGENT_FORBIDDEN_TOOLS, CURRENT_MCP_TOOLS
from app.services.payment_service import PaymentService
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from tests.identity_fixtures import VERIFIED_TEST_ISSUER, verified_human
from tests.integration.postgres_support import alembic_config

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TRUSTED_ISSUERS = frozenset({VERIFIED_TEST_ISSUER})
PREVIOUS_REVISION = "0009_payment_execution_claims"
FOREIGN_KEY_VIOLATION = "23503"
CHECK_VIOLATION = "23514"
RESTRICT_VIOLATION = "23001"
PAYMENT_PAYLOAD: dict[str, Any] = {
    "phone_number": "254700000000",
    "amount": 75_000,
    "account_reference": "INV-E2",
    "description": "Supplier settlement",
    "idempotency_key": "idem-e2",
}


@pytest.fixture(autouse=True)
def no_payment_provider(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fail the test if recording evidence reaches any payment provider or execution path."""

    calls: list[str] = []

    def forbidden(name: str) -> Callable[..., Any]:
        def _call(*_args: object, **_kwargs: object) -> Any:
            calls.append(name)
            raise AssertionError(f"payment path must not be called: {name}")

        return _call

    for owner, attribute in (
        (DarajaPaymentProvider, "initiate_payment"),
        (DarajaPaymentProvider, "check_transaction_status"),
        (MockDarajaClient, "initiate_stk_push"),
        (PaymentService, "execute_approved_payment"),
        (PaymentService, "initiate_stk_push"),
    ):
        monkeypatch.setattr(owner, attribute, forbidden(f"{owner.__name__}.{attribute}"))
    return calls


@pytest.fixture
def approvals(session_factory: sessionmaker[Session]) -> PostgresApprovalRepository:
    return PostgresApprovalRepository(session_factory, trusted_reviewer_issuers=TRUSTED_ISSUERS)


@pytest.fixture
def claims(session_factory: sessionmaker[Session]) -> PostgresExecutionClaimRepository:
    return PostgresExecutionClaimRepository(session_factory, worker_id="worker-e2")


@pytest.fixture
def evidence(session_factory: sessionmaker[Session]) -> PostgresExecutionEvidenceRepository:
    return PostgresExecutionEvidenceRepository(session_factory)


@pytest.fixture
def make_evidence(postgres_url: str) -> Iterator[Callable[[], PostgresExecutionEvidenceRepository]]:
    """Build evidence repositories on independent engines (separate connection pools)."""

    engines: list[Engine] = []

    def factory() -> PostgresExecutionEvidenceRepository:
        database_engine = create_engine(postgres_url, pool_size=2)
        engines.append(database_engine)
        return PostgresExecutionEvidenceRepository(
            sessionmaker(bind=database_engine, autoflush=False, expire_on_commit=False)
        )

    yield factory
    for database_engine in engines:
        database_engine.dispose()


def _claimed(
    approvals: PostgresApprovalRepository, claims: PostgresExecutionClaimRepository
) -> ClaimOutcome:
    with approvals._session_factory() as session:  # noqa: SLF001 - database clock
        now = session.execute(text("SELECT clock_timestamp()")).scalar_one()
    record = approvals.create(
        action="initiate_stk_push",
        payload=PAYMENT_PAYLOAD,
        reason="High-value payment requires approval",
        created_at=now - timedelta(hours=1),
        expires_at=now + timedelta(minutes=30),
        required_reviewers=1,
        policy_version="approval-policy/v1",
        policy_snapshot={"required_reviewers": 1},
        requested_by="mcp_agent",
    )
    approval_id = record.approval.approval_id
    approvals.record_decision(
        approval_id, reviewer=verified_human(f"approver-{uuid4()}"), decision="approve"
    )
    return claims.claim_execution(approval_id)


def _submitted(
    evidence: PostgresExecutionEvidenceRepository, claim: ClaimOutcome
) -> ExecutionAttemptRecord:
    attempt = evidence.record_attempt(claim.claim.claim_id, claim_token=claim.claim_token)
    evidence.record_submission_started(attempt.attempt_id, claim_token=claim.claim_token)
    return attempt


def _callback(
    evidence: PostgresExecutionEvidenceRepository,
    attempt_id: str,
    *,
    key: str = "callback-1",
    result_code: str = "0",
    **fields: Any,
) -> EvidenceOutcome:
    return evidence.record_provider_observation(
        attempt_id,
        source="provider_callback",
        observation_key=key,
        result_code=result_code,
        result_description=fields.pop("result_description", "Processed"),
        checkout_request_id=fields.pop("checkout_request_id", "ws_CO_E2"),
        **fields,
    )


def _rows(engine: Engine, table: str, claim_id: str) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                text(f"SELECT * FROM {table} WHERE claim_id = :id ORDER BY 1"), {"id": claim_id}
            ).mappings()
        ]


def _protected_state(engine: Engine, approval_id: str) -> tuple[Any, ...]:
    with engine.connect() as connection:
        return tuple(
            tuple(tuple(row) for row in connection.execute(text(query), {"id": approval_id}))
            for query in (
                "SELECT * FROM approval_requests WHERE approval_id = :id",
                "SELECT * FROM approval_decisions WHERE approval_id = :id ORDER BY decision_id",
                "SELECT * FROM payment_execution_claims WHERE approval_id = :id",
            )
        )


def _database_now(engine: Engine) -> Any:
    with engine.connect() as connection:
        return connection.execute(text("SELECT clock_timestamp()")).scalar_one()


def _run_concurrently(calls: list[Callable[[], Any]]) -> list[Any]:
    results: list[Any] = [RuntimeError("not run")] * len(calls)
    ready = threading.Barrier(len(calls))

    def worker(index: int) -> None:
        ready.wait()
        try:
            results[index] = calls[index]()
        except BaseException as exc:  # noqa: BLE001 - collected for assertions
            results[index] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(len(calls))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "worker did not finish"
    return results


# --- Binding, ownership and provenance ------------------------------------------------------


def test_attempt_and_evidence_are_bound_to_the_claim(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    evidence: PostgresExecutionEvidenceRepository,
    engine: Engine,
) -> None:
    claim = _claimed(approvals, claims)
    before = _database_now(engine)

    attempt = evidence.record_attempt(
        claim.claim.claim_id, claim_token=claim.claim_token, correlation_id="corr-e2"
    )
    started = evidence.record_submission_started(attempt.attempt_id, claim_token=claim.claim_token)
    initiation = evidence.record_provider_observation(
        attempt.attempt_id,
        source="provider_initiation_response",
        observation_key="initiation",
        result_code="0",
        result_description="Success. Request accepted for processing",
        checkout_request_id="ws_CO_E2",
        merchant_request_id="mr-E2",
        claim_token=claim.claim_token,
    )
    callback = _callback(
        evidence, attempt.attempt_id, merchant_request_id="mr-E2", provider_transaction_id="RCPT1"
    )
    after = _database_now(engine)

    assert attempt.claim_id == claim.claim.claim_id
    assert attempt.approval_id == claim.claim.approval_id
    assert attempt.payload_hash == claim.claim.payload_hash
    assert attempt.recorded_by == claim.claim.claimed_by == "worker-e2"
    assert attempt.idempotency_key == "idem-e2"
    assert (attempt.provider, attempt.rail, attempt.correlation_id) == (
        "daraja",
        "mpesa",
        "corr-e2",
    )

    rows = evidence.list_evidence(claim.claim.claim_id)
    assert rows == (started.evidence, initiation.evidence, callback.evidence)
    assert [row.sequence for row in rows] == [1, 2, 3]
    assert [(row.evidence_type, row.evidence_source) for row in rows] == [
        ("submission_started", "execution_worker"),
        ("provider_response_observed", "provider_initiation_response"),
        ("provider_response_observed", "provider_callback"),
    ]
    for row in rows:
        assert (row.attempt_id, row.claim_id, row.approval_id, row.payload_hash) == (
            attempt.attempt_id,
            attempt.claim_id,
            attempt.approval_id,
            attempt.payload_hash,
        )
        assert row.verification_level == "unverified"
    assert callback.evidence.provider_transaction_id == "RCPT1"

    # Timestamps come from the database clock and follow recording order.
    stamps = [attempt.recorded_at, *(row.recorded_at for row in rows)]
    assert before <= stamps[0] and stamps[-1] <= after
    assert stamps == sorted(stamps)
    assert "recorded_at" not in pyinspect.signature(evidence.record_provider_observation).parameters


def test_claim_token_is_required_and_never_stored(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    evidence: PostgresExecutionEvidenceRepository,
    engine: Engine,
) -> None:
    claim = _claimed(approvals, claims)
    other = _claimed(approvals, claims)

    for token in (other.claim_token, "", "forged-token"):
        with pytest.raises(ClaimOwnershipError):
            evidence.record_attempt(claim.claim.claim_id, claim_token=token)
    assert evidence.get_attempt(claim.claim.claim_id) is None

    attempt = evidence.record_attempt(claim.claim.claim_id, claim_token=claim.claim_token)
    with pytest.raises(ClaimOwnershipError):
        evidence.record_submission_started(attempt.attempt_id, claim_token=other.claim_token)
    evidence.record_submission_started(attempt.attempt_id, claim_token=claim.claim_token)
    with pytest.raises(ClaimOwnershipError):
        evidence.record_outcome_unknown(
            attempt.attempt_id, claim_token=other.claim_token, reason="timeout"
        )
    with pytest.raises(ClaimOwnershipError):
        evidence.record_provider_observation(
            attempt.attempt_id,
            source="provider_initiation_response",
            observation_key="initiation",
            result_code="0",
            claim_token=other.claim_token,
        )
    _callback(evidence, attempt.attempt_id)

    stored = [
        str(value)
        for table in ("payment_execution_attempts", "payment_execution_evidence")
        for row in _rows(engine, table, claim.claim.claim_id)
        for value in row.values()
    ]
    assert claim.claim_token not in stored
    assert all(claim.claim_token not in value for value in stored)
    assert len(evidence.list_evidence(claim.claim.claim_id)) == 2


def test_unknown_claims_and_attempts_are_refused(
    evidence: PostgresExecutionEvidenceRepository,
) -> None:
    with pytest.raises(ExecutionClaimNotFoundError):
        evidence.record_attempt(str(uuid4()), claim_token="any")
    with pytest.raises(ExecutionClaimNotFoundError):
        evidence.list_evidence(str(uuid4()))
    with pytest.raises(ExecutionClaimNotFoundError):
        evidence.assess(str(uuid4()))
    with pytest.raises(ExecutionAttemptNotFoundError):
        evidence.record_submission_started(str(uuid4()), claim_token="any")
    with pytest.raises(ExecutionAttemptNotFoundError):
        _callback(evidence, str(uuid4()))
    assert evidence.get_attempt(str(uuid4())) is None


def test_only_one_attempt_per_claim(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    evidence: PostgresExecutionEvidenceRepository,
    engine: Engine,
) -> None:
    claim = _claimed(approvals, claims)
    attempt = evidence.record_attempt(claim.claim.claim_id, claim_token=claim.claim_token)

    with pytest.raises(AttemptAlreadyRecordedError):
        evidence.record_attempt(claim.claim.claim_id, claim_token=claim.claim_token)

    assert evidence.get_attempt(claim.claim.claim_id) == attempt
    assert len(_rows(engine, "payment_execution_attempts", claim.claim.claim_id)) == 1


def test_evidence_requires_submission_started_first(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    evidence: PostgresExecutionEvidenceRepository,
) -> None:
    claim = _claimed(approvals, claims)
    attempt = evidence.record_attempt(claim.claim.claim_id, claim_token=claim.claim_token)

    with pytest.raises(SubmissionNotStartedError):
        _callback(evidence, attempt.attempt_id)
    with pytest.raises(SubmissionNotStartedError):
        evidence.record_outcome_unknown(
            attempt.attempt_id, claim_token=claim.claim_token, reason="timeout"
        )
    assert evidence.list_evidence(claim.claim.claim_id) == ()
    assert evidence.assess(claim.claim.claim_id).stage == "attempt_recorded"


@pytest.mark.parametrize(
    "call",
    [
        pytest.param({"observation_key": ""}, id="empty-key"),
        pytest.param({"observation_key": " padded"}, id="untrimmed-key"),
        pytest.param({"observation_key": "k" * 161}, id="long-key"),
        pytest.param({"result_code": ""}, id="empty-code"),
        pytest.param({"result_code": "9" * 33}, id="long-code"),
        pytest.param({"checkout_request_id": "x" * 129}, id="long-checkout-id"),
        pytest.param({"result_description": "a\x00b"}, id="nul-character"),
        pytest.param({"source": "provider_verified"}, id="unknown-source"),
        pytest.param({"source": "execution_worker"}, id="worker-source"),
        pytest.param({"claim_token": "token"}, id="token-on-callback"),
        pytest.param({"source": "provider_initiation_response"}, id="initiation-without-token"),
    ],
)
def test_invalid_observations_are_refused(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    evidence: PostgresExecutionEvidenceRepository,
    call: dict[str, Any],
) -> None:
    claim = _claimed(approvals, claims)
    attempt = _submitted(evidence, claim)
    arguments: dict[str, Any] = {
        "source": "provider_callback",
        "observation_key": "callback-1",
        "result_code": "0",
        **call,
    }

    with pytest.raises(InvalidEvidenceError):
        evidence.record_provider_observation(attempt.attempt_id, **arguments)
    with pytest.raises(InvalidEvidenceError):
        evidence.record_outcome_unknown(
            attempt.attempt_id,
            claim_token=claim.claim_token,
            reason="assumed_success",  # type: ignore[arg-type]
        )
    assert len(evidence.list_evidence(claim.claim.claim_id)) == 1


# --- Duplicates and conflicts ----------------------------------------------------------------


def test_identical_duplicates_are_idempotent(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    evidence: PostgresExecutionEvidenceRepository,
) -> None:
    claim = _claimed(approvals, claims)
    attempt = _submitted(evidence, claim)
    first = _callback(evidence, attempt.attempt_id)

    replay = _callback(evidence, attempt.attempt_id)
    again = evidence.record_submission_started(attempt.attempt_id, claim_token=claim.claim_token)

    assert first.created is True
    assert replay.created is False
    assert replay.evidence == first.evidence
    assert again.created is False
    assert len(evidence.list_evidence(claim.claim.claim_id)) == 2


def test_conflicting_duplicate_is_refused_and_original_kept(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    evidence: PostgresExecutionEvidenceRepository,
) -> None:
    claim = _claimed(approvals, claims)
    attempt = _submitted(evidence, claim)
    original = _callback(evidence, attempt.attempt_id, result_code="1032")
    evidence.record_outcome_unknown(
        attempt.attempt_id, claim_token=claim.claim_token, reason="timeout"
    )

    with pytest.raises(ConflictingEvidenceError):
        _callback(evidence, attempt.attempt_id, result_code="0")
    with pytest.raises(ConflictingEvidenceError):
        _callback(evidence, attempt.attempt_id, result_code="1032", provider_transaction_id="R")
    with pytest.raises(ConflictingEvidenceError):
        evidence.record_outcome_unknown(
            attempt.attempt_id, claim_token=claim.claim_token, reason="connection_error"
        )

    rows = evidence.list_evidence(claim.claim.claim_id)
    assert original.evidence in rows
    assert [row.result_code for row in rows if row.result_code] == ["1032"]
    assert len(rows) == 3


def test_disagreeing_observations_are_both_kept_and_flagged(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    evidence: PostgresExecutionEvidenceRepository,
) -> None:
    claim = _claimed(approvals, claims)
    attempt = _submitted(evidence, claim)
    _callback(evidence, attempt.attempt_id, key="callback-a", result_code="0")
    _callback(evidence, attempt.attempt_id, key="callback-b", result_code="1032")

    assessment = evidence.assess(claim.claim.claim_id)

    assert assessment.stage == "conflicting_evidence"
    assert assessment.conflicting is True
    assert assessment.observed_result_codes == ("0", "1032")
    assert assessment.confirmed is False
    assert assessment.resubmission_safe is False
    assert len(evidence.list_evidence(claim.claim.claim_id)) == 3


def test_disagreeing_correlation_identifiers_are_flagged(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    evidence: PostgresExecutionEvidenceRepository,
) -> None:
    claim = _claimed(approvals, claims)
    attempt = _submitted(evidence, claim)
    _callback(evidence, attempt.attempt_id, key="a", checkout_request_id="ws_CO_1")
    _callback(evidence, attempt.attempt_id, key="b", checkout_request_id="ws_CO_2")

    assert evidence.assess(claim.claim.claim_id).stage == "conflicting_evidence"


# --- Uncertainty -------------------------------------------------------------------------------


def test_uncertain_and_unverified_outcomes_are_never_confirmed(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    evidence: PostgresExecutionEvidenceRepository,
) -> None:
    claim = _claimed(approvals, claims)
    claim_id = claim.claim.claim_id

    stages = [evidence.assess(claim_id)]
    attempt = evidence.record_attempt(claim_id, claim_token=claim.claim_token)
    stages.append(evidence.assess(claim_id))
    evidence.record_submission_started(attempt.attempt_id, claim_token=claim.claim_token)
    stages.append(evidence.assess(claim_id))
    evidence.record_outcome_unknown(
        attempt.attempt_id, claim_token=claim.claim_token, reason="timeout"
    )
    stages.append(evidence.assess(claim_id))
    # A "success" result code from an unverified callback is still only an observation.
    _callback(evidence, attempt.attempt_id, result_code="0", provider_transaction_id="RCPT")
    stages.append(evidence.assess(claim_id))

    assert [assessment.stage for assessment in stages] == [
        "no_attempt",
        "attempt_recorded",
        "submission_possible",
        "outcome_unknown",
        "provider_response_observed",
    ]
    assert all(a.confirmed is False and a.resubmission_safe is False for a in stages)
    assert {row.verification_level for row in evidence.list_evidence(claim_id)} == {"unverified"}


def test_callers_cannot_supply_an_authoritative_outcome(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    evidence: PostgresExecutionEvidenceRepository,
    engine: Engine,
) -> None:
    claim = _claimed(approvals, claims)
    attempt = _submitted(evidence, claim)
    observed = _callback(evidence, attempt.attempt_id)

    for method in (
        evidence.record_provider_observation,
        evidence.record_outcome_unknown,
        evidence.record_submission_started,
        evidence.record_attempt,
    ):
        parameters = set(pyinspect.signature(method).parameters)
        assert not parameters & {"verification_level", "status", "verified", "confirmed"}
    with pytest.raises(TypeError):
        evidence.record_provider_observation(  # type: ignore[call-arg]
            attempt.attempt_id,
            source="provider_callback",
            observation_key="forged",
            result_code="0",
            verification_level="provider_verified",
        )
    assert not any(
        name.startswith(("mark_", "confirm", "complete", "settle"))
        for name in dir(PostgresExecutionEvidenceRepository)
    )

    # Direct database writes cannot store or upgrade to a verified level either.
    with pytest.raises(IntegrityError) as raised, engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO payment_execution_evidence (evidence_id, attempt_id, claim_id,"
                " approval_id, payload_hash, sequence, evidence_type, evidence_source,"
                " verification_level, observation_key, evidence_hash, result_code)"
                " SELECT :id, attempt_id, claim_id, approval_id, payload_hash, 99,"
                " 'provider_response_observed', 'provider_callback', 'verified',"
                " 'forged', :hash, '0' FROM payment_execution_attempts WHERE attempt_id = :a"
            ),
            {"id": str(uuid4()), "hash": "a" * 64, "a": attempt.attempt_id},
        )
    assert getattr(raised.value.orig, "sqlstate", None) == CHECK_VIOLATION
    with pytest.raises(IntegrityError) as raised, engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE payment_execution_evidence SET verification_level = 'verified'"
                " WHERE evidence_id = :id"
            ),
            {"id": observed.evidence.evidence_id},
        )
    assert getattr(raised.value.orig, "sqlstate", None) in {CHECK_VIOLATION, RESTRICT_VIOLATION}


# --- Concurrency -------------------------------------------------------------------------------


def test_concurrent_attempt_recording_creates_one_attempt(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    make_evidence: Callable[[], PostgresExecutionEvidenceRepository],
    engine: Engine,
) -> None:
    claim = _claimed(approvals, claims)
    workers = [make_evidence() for _ in range(8)]

    results = _run_concurrently(
        [
            partial(w.record_attempt, claim.claim.claim_id, claim_token=claim.claim_token)
            for w in workers
        ]
    )

    created = [r for r in results if isinstance(r, ExecutionAttemptRecord)]
    assert len(created) == 1, results
    assert all(isinstance(r, AttemptAlreadyRecordedError) for r in results if r not in created)
    assert len(_rows(engine, "payment_execution_attempts", claim.claim.claim_id)) == 1


def test_concurrent_observations_cannot_corrupt_an_attempt(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    make_evidence: Callable[[], PostgresExecutionEvidenceRepository],
    evidence: PostgresExecutionEvidenceRepository,
) -> None:
    claim = _claimed(approvals, claims)
    attempt = _submitted(evidence, claim)
    workers = [make_evidence() for _ in range(12)]
    calls: list[Callable[[], Any]] = []
    for index, worker in enumerate(workers):
        if index < 4:  # the same callback delivered four times
            calls.append(partial(_callback, worker, attempt.attempt_id, key="dup"))
        elif index < 8:  # one key, four different contents
            calls.append(
                partial(_callback, worker, attempt.attempt_id, key="race", result_code=str(index))
            )
        else:  # distinct observations
            calls.append(partial(_callback, worker, attempt.attempt_id, key=f"distinct-{index}"))

    results = _run_concurrently(calls)

    dup, race, distinct = results[:4], results[4:8], results[8:]
    assert all(isinstance(r, EvidenceOutcome) for r in dup), dup
    assert sum(r.created for r in dup) == 1
    assert len({r.evidence.evidence_id for r in dup}) == 1
    assert sum(isinstance(r, EvidenceOutcome) for r in race) == 1, race
    assert sum(isinstance(r, ConflictingEvidenceError) for r in race) == 3
    assert all(isinstance(r, EvidenceOutcome) and r.created for r in distinct), distinct

    rows = evidence.list_evidence(claim.claim.claim_id)
    assert [row.sequence for row in rows] == list(range(1, len(rows) + 1))
    assert len(rows) == 1 + 1 + 1 + 4  # submission, dup, race winner, distinct
    assert len({row.observation_key for row in rows}) == len(rows)


# --- Integrity, persistence, rollback and failure ----------------------------------------------


def test_evidence_never_modifies_approval_or_claim_and_e1_uniqueness_holds(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    evidence: PostgresExecutionEvidenceRepository,
    engine: Engine,
) -> None:
    claim = _claimed(approvals, claims)
    approval_id = claim.claim.approval_id
    before = _protected_state(engine, approval_id)

    attempt = _submitted(evidence, claim)
    evidence.record_outcome_unknown(
        attempt.attempt_id, claim_token=claim.claim_token, reason="timeout"
    )
    _callback(evidence, attempt.attempt_id)

    assert _protected_state(engine, approval_id) == before
    assert claims.get_claim(approval_id) == claim.claim
    assert claim.claim.status == "claimed"
    with pytest.raises(ExecutionAlreadyClaimedError):
        claims.claim_execution(approval_id)
    with pytest.raises(ExecutionAlreadyClaimedError):
        PostgresExecutionClaimRepository(
            approvals._session_factory,  # noqa: SLF001
            worker_id="worker-late",
        ).claim_execution(approval_id)
    assert _protected_state(engine, approval_id) == before


def test_attempt_and_evidence_survive_restart(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    evidence: PostgresExecutionEvidenceRepository,
    postgres_url: str,
) -> None:
    claim = _claimed(approvals, claims)
    attempt = evidence.record_attempt(
        claim.claim.claim_id, claim_token=claim.claim_token, correlation_id="corr-restart"
    )
    evidence.record_submission_started(attempt.attempt_id, claim_token=claim.claim_token)
    recorded = evidence.list_evidence(claim.claim.claim_id)

    fresh_engine = create_engine(postgres_url)
    try:
        fresh = PostgresExecutionEvidenceRepository(sessionmaker(bind=fresh_engine))
        assert fresh.get_attempt(claim.claim.claim_id) == attempt
        assert fresh.list_evidence(claim.claim.claim_id) == recorded
    finally:
        fresh_engine.dispose()

    script = (
        "import sys\n"
        "from sqlalchemy import create_engine\n"
        "from sqlalchemy.orm import sessionmaker\n"
        "from app.payments.execution_evidence import PostgresExecutionEvidenceRepository\n"
        "engine = create_engine(sys.argv[1])\n"
        "repo = PostgresExecutionEvidenceRepository(sessionmaker(bind=engine))\n"
        "attempt = repo.get_attempt(sys.argv[2])\n"
        "stage = repo.assess(sys.argv[2]).stage\n"
        "print(attempt.attempt_id, attempt.idempotency_key, attempt.correlation_id, stage)\n"
        "engine.dispose()\n"
    )
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, "-c", script, postgres_url, claim.claim.claim_id],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPOSITORY_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPOSITORY_ROOT)},
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.split() == [
        attempt.attempt_id,
        "idem-e2",
        "corr-restart",
        "submission_possible",
    ]


def test_failure_after_insert_leaves_no_partial_evidence(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    evidence: PostgresExecutionEvidenceRepository,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claimed(approvals, claims)

    def fail(_model: object) -> Any:
        raise RuntimeError("simulated failure after flush")

    with monkeypatch.context() as patch:
        patch.setattr(execution_evidence, "_to_attempt_record", fail)
        with pytest.raises(RuntimeError, match="simulated failure"):
            evidence.record_attempt(claim.claim.claim_id, claim_token=claim.claim_token)
    assert _rows(engine, "payment_execution_attempts", claim.claim.claim_id) == []

    attempt = evidence.record_attempt(claim.claim.claim_id, claim_token=claim.claim_token)
    with monkeypatch.context() as patch:
        patch.setattr(execution_evidence, "_to_evidence_record", fail)
        with pytest.raises(RuntimeError, match="simulated failure"):
            evidence.record_submission_started(attempt.attempt_id, claim_token=claim.claim_token)
    assert _rows(engine, "payment_execution_evidence", claim.claim.claim_id) == []

    started = evidence.record_submission_started(attempt.attempt_id, claim_token=claim.claim_token)
    assert started.created is True and started.evidence.sequence == 1


def test_database_rejection_rolls_back(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    evidence: PostgresExecutionEvidenceRepository,
    engine: Engine,
) -> None:
    claim = _claimed(approvals, claims)

    with pytest.raises(InvalidEvidenceError):
        evidence.record_attempt(
            claim.claim.claim_id, claim_token=claim.claim_token, correlation_id="x" * 129
        )

    assert _rows(engine, "payment_execution_attempts", claim.claim.claim_id) == []


def test_unreachable_database_fails_closed() -> None:
    unreachable = create_engine(
        "postgresql+psycopg://nobody:nobody@127.0.0.1:1/unreachable_test",
        connect_args={"connect_timeout": 2},
    )
    try:
        repository = PostgresExecutionEvidenceRepository(sessionmaker(bind=unreachable))
        for call in (
            partial(repository.record_attempt, str(uuid4()), claim_token="t"),
            partial(repository.record_submission_started, str(uuid4()), claim_token="t"),
            partial(
                repository.record_outcome_unknown, str(uuid4()), claim_token="t", reason="timeout"
            ),
            partial(
                repository.record_provider_observation,
                str(uuid4()),
                source="provider_callback",
                observation_key="k",
                result_code="0",
            ),
            partial(repository.get_attempt, str(uuid4())),
            partial(repository.list_evidence, str(uuid4())),
            partial(repository.assess, str(uuid4())),
        ):
            with pytest.raises(InfrastructureUnavailableError):
                call()
    finally:
        unreachable.dispose()


# --- Database-level guarantees -----------------------------------------------------------------


def _assert_rejected(sqlstate: str, engine: Engine, statement: str, **params: Any) -> None:
    with pytest.raises(IntegrityError) as raised, engine.begin() as connection:
        connection.execute(text(statement), params)
    assert getattr(raised.value.orig, "sqlstate", None) == sqlstate


RAW_EVIDENCE = (
    "INSERT INTO payment_execution_evidence (evidence_id, attempt_id, claim_id, approval_id,"
    " payload_hash, sequence, evidence_type, evidence_source, verification_level,"
    " observation_key, evidence_hash, uncertainty_reason, result_code)"
    " SELECT :id, attempt_id, claim_id, approval_id, {payload_hash}, :sequence, :type, :source,"
    " 'unverified', :key, :hash, :reason, :code"
    " FROM payment_execution_attempts WHERE attempt_id = :attempt"
)


def _raw_evidence(engine: Engine, attempt_id: str, sqlstate: str, **overrides: Any) -> None:
    params: dict[str, Any] = {
        "id": str(uuid4()),
        "attempt": attempt_id,
        "sequence": 50,
        "type": "provider_response_observed",
        "source": "provider_callback",
        "key": f"raw-{uuid4()}",
        "hash": "b" * 64,
        "reason": None,
        "code": "0",
    }
    payload_hash = overrides.pop("payload_hash", "payload_hash")
    params.update(overrides)
    _assert_rejected(sqlstate, engine, RAW_EVIDENCE.format(payload_hash=payload_hash), **params)


def test_database_guards_attempts(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    evidence: PostgresExecutionEvidenceRepository,
    engine: Engine,
) -> None:
    claim = _claimed(approvals, claims)
    raw_attempt = (
        "INSERT INTO payment_execution_attempts (attempt_id, claim_id, approval_id,"
        " payload_hash, provider, rail, recorded_by)"
        " SELECT :id, claim_id, approval_id, {payload_hash}, 'daraja', 'mpesa', {recorded_by}"
        " FROM payment_execution_claims WHERE claim_id = :claim"
    )
    _assert_rejected(
        RESTRICT_VIOLATION,
        engine,
        raw_attempt.format(payload_hash="payload_hash", recorded_by="'intruder'"),
        id=str(uuid4()),
        claim=claim.claim.claim_id,
    )
    _assert_rejected(
        FOREIGN_KEY_VIOLATION,
        engine,
        raw_attempt.format(payload_hash=f"'{'0' * 64}'", recorded_by="claimed_by"),
        id=str(uuid4()),
        claim=claim.claim.claim_id,
    )

    attempt = evidence.record_attempt(claim.claim.claim_id, claim_token=claim.claim_token)
    for statement in (
        "UPDATE payment_execution_attempts SET recorded_by = 'intruder' WHERE attempt_id = :id",
        "UPDATE payment_execution_attempts SET idempotency_key = 'other' WHERE attempt_id = :id",
        "DELETE FROM payment_execution_attempts WHERE attempt_id = :id",
    ):
        _assert_rejected(RESTRICT_VIOLATION, engine, statement, id=attempt.attempt_id)
    _assert_rejected(RESTRICT_VIOLATION, engine, "TRUNCATE payment_execution_attempts CASCADE")
    _assert_rejected(
        RESTRICT_VIOLATION,
        engine,
        "DELETE FROM payment_execution_claims WHERE claim_id = :id",
        id=claim.claim.claim_id,
    )
    assert evidence.get_attempt(claim.claim.claim_id) == attempt


def test_database_guards_evidence(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    evidence: PostgresExecutionEvidenceRepository,
    engine: Engine,
) -> None:
    claim = _claimed(approvals, claims)
    attempt = evidence.record_attempt(claim.claim.claim_id, claim_token=claim.claim_token)

    _raw_evidence(engine, attempt.attempt_id, RESTRICT_VIOLATION)  # before submission_started
    evidence.record_submission_started(attempt.attempt_id, claim_token=claim.claim_token)
    observed = _callback(evidence, attempt.attempt_id)

    _raw_evidence(engine, attempt.attempt_id, FOREIGN_KEY_VIOLATION, payload_hash=f"'{'0' * 64}'")
    _raw_evidence(engine, attempt.attempt_id, CHECK_VIOLATION, source="execution_worker")
    _raw_evidence(engine, attempt.attempt_id, CHECK_VIOLATION, code=None)
    _raw_evidence(
        engine,
        attempt.attempt_id,
        CHECK_VIOLATION,
        type="outcome_unknown",
        source="execution_worker",
    )
    _raw_evidence(engine, attempt.attempt_id, CHECK_VIOLATION, type="payment_succeeded")
    _raw_evidence(engine, attempt.attempt_id, "23505", key=observed.evidence.observation_key)
    _raw_evidence(engine, attempt.attempt_id, "23505", sequence=observed.evidence.sequence)

    for statement in (
        "UPDATE payment_execution_evidence SET result_code = '1' WHERE evidence_id = :id",
        "UPDATE payment_execution_evidence SET recorded_at = clock_timestamp()"
        " WHERE evidence_id = :id",
        "DELETE FROM payment_execution_evidence WHERE evidence_id = :id",
    ):
        _assert_rejected(RESTRICT_VIOLATION, engine, statement, id=observed.evidence.evidence_id)
    _assert_rejected(RESTRICT_VIOLATION, engine, "TRUNCATE payment_execution_evidence")
    assert len(evidence.list_evidence(claim.claim.claim_id)) == 2


def test_migration_downgrade_and_upgrade_round_trip(
    postgres_url: str,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = alembic_config(postgres_url, monkeypatch)
    try:
        command.downgrade(config, PREVIOUS_REVISION)
        tables = set(inspect(engine).get_table_names())
        assert not tables & {"payment_execution_attempts", "payment_execution_evidence"}
        assert "payment_execution_claims" in tables
        constraints = {
            item["name"]
            for item in inspect(engine).get_unique_constraints("payment_execution_claims")
        }
        assert "uq_payment_execution_claims_identity" not in constraints
        with engine.connect() as connection:
            remaining = connection.execute(
                text(
                    "SELECT proname FROM pg_proc WHERE proname IN ('payment_execution_append_only',"
                    " 'payment_execution_attempts_require_owner',"
                    " 'payment_execution_evidence_require_submission')"
                )
            ).all()
        assert remaining == []
    finally:
        command.upgrade(config, "head")
        get_settings.cache_clear()

    assert {"payment_execution_attempts", "payment_execution_evidence"} <= set(
        inspect(engine).get_table_names()
    )


def test_sqlite_migration_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'evidence.sqlite'}"
    config = alembic_config(database_url, monkeypatch)
    sqlite_engine = create_engine(database_url)
    try:
        command.upgrade(config, "head")
        assert "payment_execution_evidence" in inspect(sqlite_engine).get_table_names()
        command.downgrade(config, PREVIOUS_REVISION)
        tables = set(inspect(sqlite_engine).get_table_names())
        assert "payment_execution_evidence" not in tables
        assert "payment_execution_claims" in tables
    finally:
        sqlite_engine.dispose()
        get_settings.cache_clear()


# --- Authority boundary ------------------------------------------------------------------------


def test_evidence_is_not_exposed_to_agents_or_public_interfaces() -> None:
    tool_names = {*CURRENT_MCP_TOOLS, *AGENT_FORBIDDEN_TOOLS}
    assert not any("evidence" in name or "attempt" in name for name in tool_names)
    assert not any(
        "evidence" in getattr(route, "path", "") or "attempt" in getattr(route, "path", "")
        for route in app.routes
    )
    assert not any(
        "evidence" in name or "attempt" in name for name in AppContainer.__dataclass_fields__
    )

    module = "app.payments.execution_evidence"
    exported = {"PostgresExecutionEvidenceRepository", "execution_evidence"}
    for source in (REPOSITORY_ROOT / "app").rglob("*.py"):
        if source.name == "execution_evidence.py":
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module != module, source
                assert not (
                    node.module == "app.payments"
                    and any(alias.name == "execution_evidence" for alias in node.names)
                ), source
            elif isinstance(node, ast.Import):
                assert all(alias.name != module for alias in node.names), source
            elif isinstance(node, ast.Name | ast.Attribute):
                name = node.id if isinstance(node, ast.Name) else node.attr
                assert name not in exported, source


def test_recording_evidence_never_calls_a_payment_provider(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    evidence: PostgresExecutionEvidenceRepository,
    no_payment_provider: list[str],
) -> None:
    claim = _claimed(approvals, claims)
    attempt = _submitted(evidence, claim)
    evidence.record_outcome_unknown(
        attempt.attempt_id, claim_token=claim.claim_token, reason="timeout"
    )
    _callback(evidence, attempt.attempt_id)
    evidence.assess(claim.claim.claim_id)

    assert no_payment_provider == []
    assert isinstance(evidence.list_evidence(claim.claim.claim_id)[0], ExecutionEvidenceRecord)
