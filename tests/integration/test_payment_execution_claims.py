"""PostgreSQL tests for atomic durable payment execution claims (Slice E1).

A claim is created in one READ COMMITTED transaction that locks the approval row
``FOR UPDATE``, re-validates payload integrity, ``approved`` status and quorum from
persisted decisions, and inserts the single claim allowed by a unique constraint. These
tests prove exactly one worker wins, losers get no execution authority, failures leave
no partial state, approvals are never modified, and nothing reaches a payment provider
or an external interface.

Concurrency tests run every worker on its own engine (independent connections and
pools). Fixtures live in ``tests/integration/conftest.py``; these tests skip unless
``MPESA_TEST_POSTGRES_URL`` points at a local disposable ``*_test`` database.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from app.approvals.integrity import canonical_payload_hash
from app.approvals.postgres_repository import (
    ApprovalIntegrityError,
    ApprovalPersistenceError,
    ApprovalRequestNotFoundError,
    PostgresApprovalRepository,
)
from app.bootstrap.container import AppContainer
from app.config import get_settings
from app.daraja.client import MockDarajaClient
from app.infrastructure.health import InfrastructureUnavailableError
from app.main import app
from app.payments import execution_claims
from app.payments.execution_claims import (
    EXECUTABLE_PAYMENT_ACTIONS,
    ApprovalNotApprovedError,
    ApprovalQuorumNotMetError,
    ClaimOutcome,
    ExecutionAlreadyClaimedError,
    ExecutionClaimRecord,
    PostgresExecutionClaimRepository,
    UnsupportedExecutionActionError,
    claim_token_hash,
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
PREVIOUS_REVISION = "0008_durable_approvals"
UNIQUE_VIOLATION = "23505"
FOREIGN_KEY_VIOLATION = "23503"
CHECK_VIOLATION = "23514"
RESTRICT_VIOLATION = "23001"
PAYMENT_PAYLOAD: dict[str, Any] = {
    "phone_number": "254700000000",
    "amount": 75_000,
    "account_reference": "INV-E1",
    "description": "Supplier settlement",
    "idempotency_key": "idem-e1",
}
POLICY_SNAPSHOT: dict[str, Any] = {
    "required_reviewers": 1,
    "decision_authority": "verified_human_oidc",
    "minimum_reviewer_role": "approver",
}


@pytest.fixture(autouse=True)
def no_payment_provider(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fail the test if claiming reaches any payment provider or execution path."""

    calls: list[str] = []

    def forbidden(name: str) -> Callable[..., Any]:
        def _call(*_args: object, **_kwargs: object) -> Any:
            calls.append(name)
            raise AssertionError(f"payment path must not be called: {name}")

        return _call

    for owner, attribute in (
        (DarajaPaymentProvider, "initiate_payment"),
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
    return PostgresExecutionClaimRepository(session_factory, worker_id="worker-primary")


@pytest.fixture
def make_claims(
    postgres_url: str,
) -> Iterator[Callable[[str], PostgresExecutionClaimRepository]]:
    """Build claim repositories on independent engines (separate connection pools)."""

    engines: list[Engine] = []

    def factory(worker_id: str) -> PostgresExecutionClaimRepository:
        database_engine = create_engine(postgres_url, pool_size=2)
        engines.append(database_engine)
        return PostgresExecutionClaimRepository(
            sessionmaker(bind=database_engine, autoflush=False, expire_on_commit=False),
            worker_id=worker_id,
        )

    yield factory
    for database_engine in engines:
        database_engine.dispose()


def _create(
    approvals: PostgresApprovalRepository,
    *,
    required_reviewers: int = 1,
    expires_in: timedelta = timedelta(minutes=30),
    action: str = "initiate_stk_push",
) -> str:
    with approvals._session_factory() as session:  # noqa: SLF001 - database clock
        now = session.execute(text("SELECT clock_timestamp()")).scalar_one()
    record = approvals.create(
        action=action,
        payload=PAYMENT_PAYLOAD,
        reason="High-value payment requires approval",
        created_at=now - timedelta(hours=2),
        expires_at=now + expires_in,
        required_reviewers=required_reviewers,
        policy_version="approval-policy/v1",
        policy_snapshot={**POLICY_SNAPSHOT, "required_reviewers": required_reviewers},
        requested_by="mcp_agent",
        correlation_id="corr-e1",
    )
    return record.approval.approval_id


def _approve(approvals: PostgresApprovalRepository, approval_id: str, count: int) -> None:
    for index in range(count):
        approvals.record_decision(
            approval_id,
            reviewer=verified_human(f"approver-{index}-{uuid4()}"),
            decision="approve",
        )


def _approved(approvals: PostgresApprovalRepository, *, required_reviewers: int = 1) -> str:
    approval_id = _create(approvals, required_reviewers=required_reviewers)
    _approve(approvals, approval_id, required_reviewers)
    return approval_id


def _claim_rows(engine: Engine, approval_id: str) -> list[Any]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                text("SELECT * FROM payment_execution_claims WHERE approval_id = :id"),
                {"id": approval_id},
            ).mappings()
        )


def _approval_snapshot(engine: Engine, approval_id: str) -> tuple[Any, ...]:
    """Every persisted column of the request and its decisions."""

    with engine.connect() as connection:
        request = connection.execute(
            text("SELECT * FROM approval_requests WHERE approval_id = :id"),
            {"id": approval_id},
        ).one()
        decisions = connection.execute(
            text("SELECT * FROM approval_decisions WHERE approval_id = :id ORDER BY decision_id"),
            {"id": approval_id},
        ).all()
    return (tuple(request), tuple(tuple(row) for row in decisions))


def _force_status(
    engine: Engine,
    approval_id: str,
    status: str,
    decisions: list[dict[str, Any]] | None = None,
) -> None:
    """Forge a resolved request with chosen raw decision rows (bypasses the repository).

    The D1 trigger still applies: only a ``pending`` request with ``version + 1`` moves.
    """

    with engine.begin() as connection:
        payload_hash = connection.execute(
            text("SELECT payload_hash FROM approval_requests WHERE approval_id = :id"),
            {"id": approval_id},
        ).scalar_one()
        for values in decisions or []:
            row = {
                "decision_id": str(uuid4()),
                "approval_id": approval_id,
                "payload_hash": payload_hash,
                "decision": "approve",
                "reviewer_issuer": VERIFIED_TEST_ISSUER,
                "reviewer_subject": f"subject-{uuid4()}",
                "reviewer_roles": '["approver"]',
                "authentication_method": "oidc_jwks",
                "principal_type": "human",
                **values,
            }
            connection.execute(
                text(
                    f"INSERT INTO approval_decisions ({', '.join(row)}) "
                    f"VALUES ({', '.join(':' + key for key in row)})"
                ),
                row,
            )
        connection.execute(
            text(
                "UPDATE approval_requests SET status = :status, resolved_at = clock_timestamp(),"
                " version = version + 1 WHERE approval_id = :id"
            ),
            {"status": status, "id": approval_id},
        )


def _run_concurrently(
    calls: list[Callable[[], Any]],
    *,
    lock_engine: Engine | None = None,
    approval_id: str | None = None,
) -> list[Any]:
    """Start ``calls`` together; optionally hold the approval row lock briefly first."""

    results: list[Any] = [RuntimeError("not run")] * len(calls)
    ready = threading.Barrier(len(calls) + 1)

    def worker(index: int) -> None:
        ready.wait()
        try:
            results[index] = calls[index]()
        except BaseException as exc:  # noqa: BLE001 - collected for assertions
            results[index] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(len(calls))]
    if lock_engine is None:
        for thread in threads:
            thread.start()
        ready.wait()
    else:
        # Every worker queues on the row lock, then they race the moment it is released.
        with lock_engine.connect() as holder:
            transaction = holder.begin()
            holder.execute(
                text("SELECT 1 FROM approval_requests WHERE approval_id = :id FOR UPDATE"),
                {"id": approval_id},
            )
            for thread in threads:
                thread.start()
            ready.wait()
            time.sleep(0.5)
            transaction.rollback()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "worker did not finish"
    return results


def _assert_no_authority(error: BaseException, winner: ClaimOutcome) -> None:
    assert isinstance(error, ExecutionAlreadyClaimedError)
    rendered = f"{error!s} {error!r} {vars(error)}"
    assert winner.claim_token not in rendered
    assert winner.claim.claim_id not in rendered
    assert winner.claim.claimed_by not in rendered
    assert not hasattr(error, "claim_token")


# --- Successful claims -----------------------------------------------------------------


def test_approved_request_acquires_one_claim(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    engine: Engine,
) -> None:
    approval_id = _approved(approvals)
    with engine.connect() as connection:
        before = connection.execute(text("SELECT clock_timestamp()")).scalar_one()

    outcome = claims.claim_execution(approval_id, correlation_id="corr-claim")

    with engine.connect() as connection:
        after = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
    claim = outcome.claim
    record = approvals.get(approval_id)
    assert record is not None
    assert claim.approval_id == approval_id
    assert claim.payload_hash == record.payload_hash == canonical_payload_hash(PAYMENT_PAYLOAD)
    assert claim.status == "claimed"
    assert claim.claimed_by == "worker-primary"
    assert claim.correlation_id == "corr-claim"
    assert before <= claim.claimed_at <= after
    assert len(outcome.claim_token) >= 43  # 32 random bytes, URL-safe base64
    assert outcome.claim_token not in repr(outcome)

    rows = _claim_rows(engine, approval_id)
    assert len(rows) == 1
    assert rows[0]["claim_id"] == claim.claim_id
    assert rows[0]["claim_token_hash"] == claim_token_hash(outcome.claim_token)
    assert rows[0]["claim_token_hash"] == hashlib.sha256(outcome.claim_token.encode()).hexdigest()
    assert outcome.claim_token not in {str(value) for value in rows[0].values()}
    assert claims.get_claim(approval_id) == claim
    assert "claim_token" not in ExecutionClaimRecord.model_fields
    assert "claim_token_hash" not in ExecutionClaimRecord.model_fields


@pytest.mark.parametrize("required_reviewers", [2, 3])
def test_quorum_of_distinct_humans_is_required_before_claiming(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    engine: Engine,
    required_reviewers: int,
) -> None:
    approval_id = _create(approvals, required_reviewers=required_reviewers)

    for _ in range(required_reviewers - 1):
        _approve(approvals, approval_id, 1)
        with pytest.raises(ApprovalNotApprovedError) as refused:
            claims.claim_execution(approval_id)
        assert refused.value.status == "pending"
        assert _claim_rows(engine, approval_id) == []

    _approve(approvals, approval_id, 1)
    outcome = claims.claim_execution(approval_id)

    record = approvals.get(approval_id)
    assert record is not None
    assert record.approval.status == "approved"
    assert record.approval.review_count == required_reviewers
    assert outcome.claim.approval_id == approval_id
    assert len(_claim_rows(engine, approval_id)) == 1


def test_claims_for_different_approvals_are_independent(
    approvals: PostgresApprovalRepository,
    make_claims: Callable[[str], PostgresExecutionClaimRepository],
    engine: Engine,
) -> None:
    approval_ids = [_approved(approvals) for _ in range(6)]
    workers = [make_claims(f"worker-{index}") for index in range(6)]

    results = _run_concurrently(
        [
            lambda w=worker, a=approval_id: w.claim_execution(a)  # type: ignore[misc]
            for worker, approval_id in zip(workers, approval_ids, strict=True)
        ]
    )

    assert all(isinstance(result, ClaimOutcome) for result in results), results
    for index, (result, approval_id) in enumerate(zip(results, approval_ids, strict=True)):
        assert result.claim.approval_id == approval_id
        assert result.claim.claimed_by == f"worker-{index}"
    assert len({result.claim_token for result in results}) == 6


def test_claim_on_one_approval_does_not_wait_for_another_approvals_lock(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    engine: Engine,
) -> None:
    locked_id = _approved(approvals)
    free_id = _approved(approvals)

    with engine.connect() as holder:
        transaction = holder.begin()
        holder.execute(
            text("SELECT 1 FROM approval_requests WHERE approval_id = :id FOR UPDATE"),
            {"id": locked_id},
        )
        started = time.monotonic()
        outcome = claims.claim_execution(free_id)
        elapsed = time.monotonic() - started
        transaction.rollback()

    assert outcome.claim.approval_id == free_id
    assert elapsed < 2.0
    assert _claim_rows(engine, locked_id) == []


# --- Refusals ----------------------------------------------------------------------------


def test_pending_request_cannot_be_claimed(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    engine: Engine,
) -> None:
    approval_id = _create(approvals)

    with pytest.raises(ApprovalNotApprovedError) as refused:
        claims.claim_execution(approval_id)

    assert refused.value.status == "pending"
    assert _claim_rows(engine, approval_id) == []


def test_rejected_request_cannot_be_claimed(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    engine: Engine,
) -> None:
    approval_id = _create(approvals)
    approvals.record_decision(approval_id, reviewer=verified_human("rejecter"), decision="reject")

    with pytest.raises(ApprovalNotApprovedError) as refused:
        claims.claim_execution(approval_id)

    assert refused.value.status == "rejected"
    assert _claim_rows(engine, approval_id) == []


def test_expired_request_cannot_be_claimed(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    engine: Engine,
) -> None:
    approval_id = _create(approvals, expires_in=-timedelta(minutes=5))
    assert approvals.expire_request(approval_id).expired is True

    with pytest.raises(ApprovalNotApprovedError) as refused:
        claims.claim_execution(approval_id)

    assert refused.value.status == "expired"
    assert _claim_rows(engine, approval_id) == []


def test_overdue_but_unexpired_pending_request_cannot_be_claimed(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    engine: Engine,
) -> None:
    approval_id = _create(approvals, expires_in=-timedelta(minutes=5))

    with pytest.raises(ApprovalNotApprovedError):
        claims.claim_execution(approval_id)

    assert _claim_rows(engine, approval_id) == []


def test_unknown_approval_cannot_be_claimed(claims: PostgresExecutionClaimRepository) -> None:
    with pytest.raises(ApprovalRequestNotFoundError):
        claims.claim_execution(str(uuid4()))
    assert claims.get_claim(str(uuid4())) is None


@pytest.mark.parametrize(
    "decisions",
    [
        pytest.param([], id="no-decisions"),
        pytest.param([{"principal_type": "application"}], id="application-principal"),
        pytest.param([{"principal_type": "unknown"}], id="unclassified-principal"),
        pytest.param([{"authentication_method": "static_token"}], id="static-token"),
        pytest.param([{"authentication_method": "oidc_development"}], id="development-oidc"),
        pytest.param([{"authentication_method": "auth_disabled"}], id="auth-disabled"),
        pytest.param([{}, {"decision": "reject"}], id="approval-plus-rejection"),
    ],
)
def test_forged_approval_without_verified_human_quorum_cannot_be_claimed(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    engine: Engine,
    decisions: list[dict[str, Any]],
) -> None:
    approval_id = _create(approvals)
    _force_status(engine, approval_id, "approved", decisions)

    with pytest.raises(ApprovalQuorumNotMetError):
        claims.claim_execution(approval_id)

    assert _claim_rows(engine, approval_id) == []


def test_forged_approval_with_one_of_two_required_humans_cannot_be_claimed(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    engine: Engine,
) -> None:
    approval_id = _create(approvals, required_reviewers=2)
    _force_status(engine, approval_id, "approved", [{}])

    with pytest.raises(ApprovalQuorumNotMetError):
        claims.claim_execution(approval_id)

    assert _claim_rows(engine, approval_id) == []


NON_PAYMENT_ACTIONS = (
    # Existing read-only actions from the safety policy.
    "check_transaction_status",
    "get_today_transactions",
    "get_failed_transactions",
    "generate_receipt",
    # An MCP tool name, not an approval action: the tool delegates to initiate_stk_push.
    "initiate_payment",
    # Unknown, administrative and near-miss spellings.
    "refund_payment",
    "admin_override",
    "INITIATE_STK_PUSH",
    "initiate_stk_push ",
    "initiate_stk_push_v2",
)


def test_only_the_existing_payment_action_is_executable() -> None:
    assert EXECUTABLE_PAYMENT_ACTIONS == frozenset({"initiate_stk_push"})
    assert not EXECUTABLE_PAYMENT_ACTIONS & set(NON_PAYMENT_ACTIONS)


@pytest.mark.parametrize("action", NON_PAYMENT_ACTIONS)
def test_approved_non_payment_action_cannot_be_claimed(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    engine: Engine,
    no_payment_provider: list[str],
    action: str,
) -> None:
    approval_id = _create(approvals, action=action)
    _approve(approvals, approval_id, 1)
    record = approvals.get(approval_id)
    assert record is not None
    assert record.approval.status == "approved"
    before = _approval_snapshot(engine, approval_id)

    with pytest.raises(UnsupportedExecutionActionError):
        claims.claim_execution(approval_id)

    assert _claim_rows(engine, approval_id) == []
    assert _approval_snapshot(engine, approval_id) == before
    assert no_payment_provider == []


def test_database_refuses_claims_for_non_payment_actions(
    approvals: PostgresApprovalRepository,
    engine: Engine,
) -> None:
    approval_id = _create(approvals, action="generate_receipt")
    _approve(approvals, approval_id, 1)

    _assert_rejected(
        RESTRICT_VIOLATION,
        engine,
        RAW_CLAIM_INSERT,
        **_raw_claim(approval_id, canonical_payload_hash(PAYMENT_PAYLOAD)),
    )
    assert _claim_rows(engine, approval_id) == []


def test_migration_action_allowlist_matches_the_repository() -> None:
    migration_path = (
        REPOSITORY_ROOT / "alembic" / "versions" / "0009_create_payment_execution_claims.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0009", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert frozenset(migration.EXECUTABLE_PAYMENT_ACTIONS) == EXECUTABLE_PAYMENT_ACTIONS
    assert "'initiate_stk_push'" in migration.POSTGRES_GUARD_FUNCTIONS[0]


def test_tampered_payload_cannot_be_claimed(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    engine: Engine,
) -> None:
    approval_id = _approved(approvals)

    def set_amount(amount: int) -> None:
        # Simulates a privileged actor who bypasses the D1 guard trigger.
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE approval_requests DISABLE TRIGGER trg_approval_requests_guard")
            )
            try:
                connection.execute(
                    text(
                        "UPDATE approval_requests SET payment_payload = jsonb_set("
                        "payment_payload, '{amount}', to_jsonb(CAST(:amount AS integer)))"
                        " WHERE approval_id = :id"
                    ),
                    {"amount": amount, "id": approval_id},
                )
            finally:
                connection.execute(
                    text("ALTER TABLE approval_requests ENABLE TRIGGER trg_approval_requests_guard")
                )

    set_amount(1)
    try:
        with pytest.raises(ApprovalIntegrityError):
            claims.claim_execution(approval_id)
        assert _claim_rows(engine, approval_id) == []
    finally:
        set_amount(PAYMENT_PAYLOAD["amount"])


def test_duplicate_claim_is_refused_without_exposing_the_winner(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    make_claims: Callable[[str], PostgresExecutionClaimRepository],
    engine: Engine,
) -> None:
    approval_id = _approved(approvals)
    winner = claims.claim_execution(approval_id)
    stored = _claim_rows(engine, approval_id)

    for repository in (claims, make_claims("worker-other")):
        with pytest.raises(ExecutionAlreadyClaimedError) as refused:
            repository.claim_execution(approval_id)
        _assert_no_authority(refused.value, winner)

    assert _claim_rows(engine, approval_id) == stored


# --- Concurrency -------------------------------------------------------------------------


@pytest.mark.parametrize("hold_lock", [False, True], ids=["free-start", "lock-release"])
def test_eight_workers_racing_for_one_approval_produce_one_claim(
    approvals: PostgresApprovalRepository,
    make_claims: Callable[[str], PostgresExecutionClaimRepository],
    engine: Engine,
    hold_lock: bool,
) -> None:
    approval_id = _approved(approvals, required_reviewers=2)
    before = _approval_snapshot(engine, approval_id)
    workers = [make_claims(f"race-worker-{index}") for index in range(8)]

    results = _run_concurrently(
        [lambda w=worker: w.claim_execution(approval_id) for worker in workers],  # type: ignore[misc]
        lock_engine=engine if hold_lock else None,
        approval_id=approval_id,
    )

    winners = [result for result in results if isinstance(result, ClaimOutcome)]
    losers = [result for result in results if not isinstance(result, ClaimOutcome)]
    assert len(winners) == 1, results
    assert len(losers) == 7
    winner = winners[0]
    for loser in losers:
        _assert_no_authority(loser, winner)

    rows = _claim_rows(engine, approval_id)
    assert len(rows) == 1
    assert rows[0]["claim_id"] == winner.claim.claim_id
    assert rows[0]["claimed_by"] == winner.claim.claimed_by
    assert rows[0]["claim_token_hash"] == claim_token_hash(winner.claim_token)
    assert _approval_snapshot(engine, approval_id) == before


# --- Failure, rollback and persistence ---------------------------------------------------


def test_failure_after_insert_rolls_back_the_claim(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approval_id = _approved(approvals)
    before = _approval_snapshot(engine, approval_id)

    def fail(_model: object) -> ExecutionClaimRecord:
        raise RuntimeError("simulated failure after the claim row was flushed")

    with monkeypatch.context() as patch:
        patch.setattr(execution_claims, "_to_claim_record", fail)
        with pytest.raises(RuntimeError, match="simulated failure"):
            claims.claim_execution(approval_id)

    assert _claim_rows(engine, approval_id) == []
    assert _approval_snapshot(engine, approval_id) == before
    # Nothing partial remains: the approval can still be claimed normally.
    assert claims.claim_execution(approval_id).claim.approval_id == approval_id


def test_database_rejection_of_claim_data_rolls_back(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    engine: Engine,
) -> None:
    approval_id = _approved(approvals)
    before = _approval_snapshot(engine, approval_id)

    with pytest.raises(ApprovalPersistenceError):
        claims.claim_execution(approval_id, correlation_id="x" * 129)

    assert _claim_rows(engine, approval_id) == []
    assert _approval_snapshot(engine, approval_id) == before


def test_claim_never_modifies_the_approval_or_its_decisions(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    engine: Engine,
) -> None:
    approval_id = _approved(approvals, required_reviewers=3)
    before = _approval_snapshot(engine, approval_id)
    record_before = approvals.get(approval_id)

    claims.claim_execution(approval_id)

    assert _approval_snapshot(engine, approval_id) == before
    assert approvals.get(approval_id) == record_before


def test_claim_persists_across_repository_and_process_restarts(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    postgres_url: str,
) -> None:
    approval_id = _approved(approvals)
    claim = claims.claim_execution(approval_id).claim

    fresh_engine = create_engine(postgres_url)
    try:
        fresh = PostgresExecutionClaimRepository(
            sessionmaker(bind=fresh_engine, autoflush=False, expire_on_commit=False),
            worker_id="worker-after-restart",
        )
        assert fresh.get_claim(approval_id) == claim
        with pytest.raises(ExecutionAlreadyClaimedError):
            fresh.claim_execution(approval_id)
    finally:
        fresh_engine.dispose()

    script = (
        "import sys\n"
        "from sqlalchemy import create_engine\n"
        "from sqlalchemy.orm import sessionmaker\n"
        "from app.payments.execution_claims import (\n"
        "    ExecutionAlreadyClaimedError, PostgresExecutionClaimRepository)\n"
        "engine = create_engine(sys.argv[1])\n"
        "repo = PostgresExecutionClaimRepository(sessionmaker(bind=engine),"
        " worker_id='worker-new-process')\n"
        "claim = repo.get_claim(sys.argv[2])\n"
        "try:\n"
        "    repo.claim_execution(sys.argv[2])\n"
        "    outcome = 'claimed'\n"
        "except ExecutionAlreadyClaimedError:\n"
        "    outcome = 'already_claimed'\n"
        "print(claim.claim_id, claim.claimed_by, outcome)\n"
        "engine.dispose()\n"
    )
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, "-c", script, postgres_url, approval_id],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPOSITORY_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPOSITORY_ROOT)},
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.split() == [claim.claim_id, "worker-primary", "already_claimed"]


def test_unreachable_database_fails_closed() -> None:
    unreachable = create_engine(
        "postgresql+psycopg://nobody:nobody@127.0.0.1:1/unreachable_test",
        connect_args={"connect_timeout": 2},
    )
    try:
        repository = PostgresExecutionClaimRepository(
            sessionmaker(bind=unreachable), worker_id="worker-offline"
        )
        with pytest.raises(InfrastructureUnavailableError):
            repository.claim_execution(str(uuid4()))
        with pytest.raises(InfrastructureUnavailableError):
            repository.get_claim(str(uuid4()))
    finally:
        unreachable.dispose()


@pytest.mark.parametrize("worker_id", ["", "   ", " padded", "x" * 256, None, 7])
def test_worker_identity_must_be_explicit(
    session_factory: sessionmaker[Session],
    worker_id: Any,
) -> None:
    with pytest.raises(ValueError, match="worker_id"):
        PostgresExecutionClaimRepository(session_factory, worker_id=worker_id)


# --- Database-level guarantees -----------------------------------------------------------


def _assert_rejected(sqlstate: str, engine: Engine, statement: str, **params: Any) -> None:
    with pytest.raises(IntegrityError) as raised, engine.begin() as connection:
        connection.execute(text(statement), params)
    assert getattr(raised.value.orig, "sqlstate", None) == sqlstate


def _raw_claim(approval_id: str, payload_hash: str, **overrides: Any) -> dict[str, Any]:
    return {
        "claim_id": str(uuid4()),
        "approval_id": approval_id,
        "payload_hash": payload_hash,
        "status": "claimed",
        "claim_token_hash": hashlib.sha256(uuid4().bytes).hexdigest(),
        "claimed_by": "raw-sql",
        **overrides,
    }


RAW_CLAIM_INSERT = (
    "INSERT INTO payment_execution_claims"
    " (claim_id, approval_id, payload_hash, status, claim_token_hash, claimed_by)"
    " VALUES (:claim_id, :approval_id, :payload_hash, :status, :claim_token_hash, :claimed_by)"
)


def test_database_refuses_claims_for_unapproved_requests(
    approvals: PostgresApprovalRepository,
    engine: Engine,
) -> None:
    pending_id = _create(approvals)
    payload_hash = canonical_payload_hash(PAYMENT_PAYLOAD)

    _assert_rejected(
        RESTRICT_VIOLATION, engine, RAW_CLAIM_INSERT, **_raw_claim(pending_id, payload_hash)
    )
    _assert_rejected(
        FOREIGN_KEY_VIOLATION,
        engine,
        RAW_CLAIM_INSERT,
        **_raw_claim(_approved(approvals), "0" * 64),
    )
    assert _claim_rows(engine, pending_id) == []


def test_database_enforces_one_immutable_undeletable_claim(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    engine: Engine,
) -> None:
    approval_id = _approved(approvals)
    claim = claims.claim_execution(approval_id).claim
    payload_hash = claim.payload_hash

    _assert_rejected(
        UNIQUE_VIOLATION, engine, RAW_CLAIM_INSERT, **_raw_claim(approval_id, payload_hash)
    )
    for column, value in (
        ("claimed_by", "'attacker'"),
        ("claim_token_hash", f"'{'a' * 64}'"),
        ("claimed_at", "clock_timestamp()"),
        ("correlation_id", "'rewritten'"),
        ("claim_id", f"'{uuid4()}'"),
    ):
        _assert_rejected(
            RESTRICT_VIOLATION,
            engine,
            f"UPDATE payment_execution_claims SET {column} = {value} WHERE approval_id = :id",
            id=approval_id,
        )
    _assert_rejected(
        CHECK_VIOLATION,
        engine,
        "UPDATE payment_execution_claims SET status = 'executed' WHERE approval_id = :id",
        id=approval_id,
    )
    _assert_rejected(
        RESTRICT_VIOLATION,
        engine,
        "DELETE FROM payment_execution_claims WHERE approval_id = :id",
        id=approval_id,
    )
    # CASCADE reaches the guard trigger; plain TRUNCATE is refused earlier by E2.1 FKs.
    _assert_rejected(RESTRICT_VIOLATION, engine, "TRUNCATE payment_execution_claims CASCADE")
    # The claimed approval itself cannot be deleted either (D1 guard trigger).
    _assert_rejected(
        RESTRICT_VIOLATION,
        engine,
        "DELETE FROM approval_requests WHERE approval_id = :id",
        id=approval_id,
    )
    assert claims.get_claim(approval_id) == claim


def test_migration_downgrade_and_upgrade_round_trip(
    postgres_url: str,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = alembic_config(postgres_url, monkeypatch)
    try:
        command.downgrade(config, PREVIOUS_REVISION)
        assert "payment_execution_claims" not in inspect(engine).get_table_names()
        with engine.connect() as connection:
            remaining = connection.execute(
                text("SELECT proname FROM pg_proc WHERE proname LIKE 'payment_execution_claims%'")
            ).all()
        assert remaining == []
        assert {"approval_requests", "approval_decisions"} <= set(inspect(engine).get_table_names())
    finally:
        command.upgrade(config, "head")
        get_settings.cache_clear()

    assert "payment_execution_claims" in inspect(engine).get_table_names()


def test_sqlite_migration_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'claims.sqlite'}"
    config = alembic_config(database_url, monkeypatch)
    sqlite_engine = create_engine(database_url)
    try:
        command.upgrade(config, "head")
        assert "payment_execution_claims" in inspect(sqlite_engine).get_table_names()
        command.downgrade(config, PREVIOUS_REVISION)
        assert "payment_execution_claims" not in inspect(sqlite_engine).get_table_names()
        assert "approval_requests" in inspect(sqlite_engine).get_table_names()
    finally:
        sqlite_engine.dispose()
        get_settings.cache_clear()


# --- Authority boundary ------------------------------------------------------------------


def test_claim_creation_is_not_exposed_to_agents_or_public_interfaces() -> None:
    tool_names = {*CURRENT_MCP_TOOLS, *AGENT_FORBIDDEN_TOOLS}
    assert not any("claim" in name for name in tool_names)
    assert not any("claim" in getattr(route, "path", "") for route in app.routes)
    assert not any("claim" in name for name in AppContainer.__dataclass_fields__)

    claims_module = "app.payments.execution_claims"
    exported = {"PostgresExecutionClaimRepository", "claim_execution", "execution_claims"}
    for source in (REPOSITORY_ROOT / "app").rglob("*.py"):
        # The E2.1 evidence module is internal execution infrastructure and reuses the
        # claim-token hash; it is itself scanned for exposure by its own tests.
        if source.name in {"execution_claims.py", "execution_evidence.py"}:
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module != claims_module, source
                assert not (
                    node.module == "app.payments"
                    and any(alias.name == "execution_claims" for alias in node.names)
                ), source
            elif isinstance(node, ast.Import):
                assert all(alias.name != claims_module for alias in node.names), source
            elif isinstance(node, ast.Name | ast.Attribute):
                name = node.id if isinstance(node, ast.Name) else node.attr
                assert name not in exported, source


def test_claiming_never_calls_a_payment_provider(
    approvals: PostgresApprovalRepository,
    claims: PostgresExecutionClaimRepository,
    no_payment_provider: list[str],
) -> None:
    approval_id = _approved(approvals)

    claims.claim_execution(approval_id)
    with pytest.raises(ExecutionAlreadyClaimedError):
        claims.claim_execution(approval_id)

    assert no_payment_provider == []
