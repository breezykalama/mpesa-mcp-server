"""PostgreSQL tests for atomic durable approval decisions and quorum (Slice D3).

Fixtures live in ``tests/integration/conftest.py``; these tests skip unless
``MPESA_TEST_POSTGRES_URL`` points at a local disposable ``*_test`` database.

Concurrency tests run each worker on its own engine (its own connection pool), so the
only thing serializing decisions is PostgreSQL's row lock, never a process-local lock.
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from app.approvals import postgres_repository
from app.approvals.integrity import canonical_payload_hash
from app.approvals.postgres_repository import (
    ApprovalDecisionRejectedError,
    ApprovalExpiredError,
    ApprovalNotPendingError,
    ApprovalPersistenceError,
    ApprovalRequestNotFoundError,
    DecisionOutcome,
    DuplicateReviewerDecisionError,
    DurableApprovalRecord,
    PostgresApprovalRepository,
)
from app.approvals.reviewer import ReviewerNotEligibleError
from app.auth.oidc import OperatorIdentity
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from tests.identity_fixtures import (
    VERIFIED_TEST_ISSUER,
    application_principal,
    static_token_identity,
    verified_human,
)

PAYMENT_PAYLOAD: dict[str, Any] = {
    "phone_number": "254700000000",
    "amount": 75_000,
    "account_reference": "INV-2026-001",
    "description": "Supplier settlement",
    "idempotency_key": "idem-d3",
}
POLICY_SNAPSHOT: dict[str, Any] = {
    "required_reviewers": 2,
    "high_risk_amount_threshold": 50_000,
    "eligible_roles": ["approver", "admin"],
}
TRUSTED_ISSUERS = frozenset({VERIFIED_TEST_ISSUER})


@pytest.fixture
def repository(session_factory: sessionmaker[Session]) -> PostgresApprovalRepository:
    return PostgresApprovalRepository(session_factory, trusted_reviewer_issuers=TRUSTED_ISSUERS)


def _create(
    repository: PostgresApprovalRepository,
    *,
    required_reviewers: int = 1,
    **overrides: Any,
) -> DurableApprovalRecord:
    now = datetime.now(UTC)
    values: dict[str, Any] = {
        "action": "initiate_stk_push",
        "payload": PAYMENT_PAYLOAD,
        "reason": "High-value payment requires approval",
        "expires_at": now + timedelta(minutes=30),
        "required_reviewers": required_reviewers,
        "policy_version": "approval-policy/2026-11-24",
        "policy_snapshot": {**POLICY_SNAPSHOT, "required_reviewers": required_reviewers},
        "requested_by": "mcp_agent",
    }
    values.update(overrides)
    return repository.create(**values)


def _reviewer(index: int | str) -> OperatorIdentity:
    return verified_human(
        subject=f"reviewer-{index}",
        email=f"reviewer-{index}@example.test",
        display_name=f"Reviewer {index}",
    )


def _state(engine: Engine, approval_id: str) -> tuple[str, int, list[tuple[str, str, str]]]:
    """Return (status, version, [(subject, issuer, decision)...]) straight from the DB."""

    with engine.connect() as connection:
        status, version = connection.execute(
            text("SELECT status, version FROM approval_requests WHERE approval_id = :id"),
            {"id": approval_id},
        ).one()
        rows = connection.execute(
            text(
                "SELECT reviewer_subject, reviewer_issuer, decision FROM approval_decisions"
                " WHERE approval_id = :id ORDER BY decided_at, decision_id"
            ),
            {"id": approval_id},
        ).all()
    return status, version, [(row[0], row[1], row[2]) for row in rows]


def _assert_consistent(engine: Engine, approval_id: str, required_reviewers: int) -> str:
    """Replay persisted decisions through the quorum rules and compare with stored state.

    Proves there are no duplicate reviewers, no decisions after a terminal transition,
    an exact version (1 + one per accepted decision) and a status matching the rows.
    """

    status, version, rows = _state(engine, approval_id)
    reviewers = [(subject, issuer) for subject, issuer, _ in rows]
    assert len(reviewers) == len(set(reviewers)), "duplicate reviewer decision persisted"

    replayed = "pending"
    approvals = 0
    for _, _, decision in rows:
        assert replayed == "pending", "decision persisted after a terminal transition"
        if decision == "reject":
            replayed = "rejected"
        else:
            approvals += 1
            if approvals >= required_reviewers:
                replayed = "approved"

    assert status == replayed
    assert version == 1 + len(rows)
    return status


# --- Quorum ---------------------------------------------------------------------------


def test_single_reviewer_approval_completes_request(
    repository: PostgresApprovalRepository,
    engine: Engine,
) -> None:
    created = _create(repository, required_reviewers=1)

    outcome = repository.record_decision(
        created.approval.approval_id, reviewer=_reviewer(1), decision="approve"
    )

    assert outcome.status == "approved"
    assert outcome.record.approval.status == "approved"
    assert outcome.record.approval.review_count == 1
    assert outcome.record.approval.reviewer_ids == ["reviewer-1"]
    assert outcome.record.version == 2
    assert outcome.record.resolved_at is not None
    assert outcome.record.approval.reviewed_at == outcome.record.resolved_at
    assert repository.get(created.approval.approval_id) == outcome.record
    assert _assert_consistent(engine, created.approval.approval_id, 1) == "approved"


def test_two_reviewer_quorum(repository: PostgresApprovalRepository, engine: Engine) -> None:
    approval_id = _create(repository, required_reviewers=2).approval.approval_id

    first = repository.record_decision(approval_id, reviewer=_reviewer("a"), decision="approve")
    assert first.status == "partially_approved"
    assert first.record.approval.status == "pending"
    assert first.record.approval.review_count == 1
    assert first.record.resolved_at is None
    assert first.record.version == 2

    second = repository.record_decision(approval_id, reviewer=_reviewer("b"), decision="approve")
    assert second.status == "approved"
    assert second.record.approval.status == "approved"
    assert second.record.approval.reviewer_ids == ["reviewer-a", "reviewer-b"]
    assert second.record.version == 3
    assert _assert_consistent(engine, approval_id, 2) == "approved"


def test_three_reviewer_quorum(repository: PostgresApprovalRepository, engine: Engine) -> None:
    approval_id = _create(repository, required_reviewers=3).approval.approval_id

    outcomes = [
        repository.record_decision(approval_id, reviewer=_reviewer(index), decision="approve")
        for index in range(3)
    ]

    assert [outcome.status for outcome in outcomes] == [
        "partially_approved",
        "partially_approved",
        "approved",
    ]
    assert [outcome.record.version for outcome in outcomes] == [2, 3, 4]
    assert [outcome.record.approval.review_count for outcome in outcomes] == [1, 2, 3]
    assert _assert_consistent(engine, approval_id, 3) == "approved"


def test_quorum_uses_snapshotted_required_reviewers(
    repository: PostgresApprovalRepository,
) -> None:
    created = _create(repository, required_reviewers=2)

    outcome = repository.record_decision(
        created.approval.approval_id, reviewer=_reviewer(1), decision="approve"
    )

    assert outcome.record.approval.required_reviewers == 2
    assert outcome.status == "partially_approved"


@pytest.mark.parametrize("required_reviewers", [1, 2])
def test_rejection_is_terminal(
    repository: PostgresApprovalRepository,
    engine: Engine,
    required_reviewers: int,
) -> None:
    approval_id = _create(repository, required_reviewers=required_reviewers).approval.approval_id
    if required_reviewers == 2:
        repository.record_decision(approval_id, reviewer=_reviewer("a"), decision="approve")

    outcome = repository.record_decision(
        approval_id, reviewer=_reviewer("r"), decision="reject", comment="Amount mismatch"
    )

    assert outcome.status == "rejected"
    assert outcome.record.approval.status == "rejected"
    assert outcome.record.resolved_at is not None
    with pytest.raises(ApprovalNotPendingError):
        repository.record_decision(approval_id, reviewer=_reviewer("late"), decision="approve")
    assert _assert_consistent(engine, approval_id, required_reviewers) == "rejected"


# --- Duplicate and terminal-state refusals ----------------------------------------------


@pytest.mark.parametrize("second_decision", ["approve", "reject"])
def test_reviewer_cannot_decide_twice(
    repository: PostgresApprovalRepository,
    engine: Engine,
    second_decision: str,
) -> None:
    approval_id = _create(repository, required_reviewers=2).approval.approval_id
    repository.record_decision(approval_id, reviewer=_reviewer("a"), decision="approve")

    with pytest.raises(DuplicateReviewerDecisionError):
        repository.record_decision(
            approval_id,
            reviewer=_reviewer("a"),
            decision=second_decision,  # type: ignore[arg-type]
        )

    status, version, rows = _state(engine, approval_id)
    assert (status, version, len(rows)) == ("pending", 2, 1)


def test_duplicate_detection_uses_issuer_and_subject(
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    other_issuer = "https://other-idp.example.test"
    repository = PostgresApprovalRepository(
        session_factory, trusted_reviewer_issuers={VERIFIED_TEST_ISSUER, other_issuer}
    )
    approval_id = _create(repository, required_reviewers=2).approval.approval_id

    repository.record_decision(approval_id, reviewer=_reviewer("same"), decision="approve")
    outcome = repository.record_decision(
        approval_id,
        reviewer=verified_human(subject="reviewer-same", issuer=other_issuer),
        decision="approve",
    )

    assert outcome.status == "approved"
    _, _, rows = _state(engine, approval_id)
    assert {(subject, issuer) for subject, issuer, _ in rows} == {
        ("reviewer-same", VERIFIED_TEST_ISSUER),
        ("reviewer-same", other_issuer),
    }


@pytest.mark.parametrize("terminal", ["approved", "rejected"])
def test_terminal_requests_refuse_decisions(
    repository: PostgresApprovalRepository,
    engine: Engine,
    terminal: str,
) -> None:
    approval_id = _create(repository, required_reviewers=1).approval.approval_id
    repository.record_decision(
        approval_id,
        reviewer=_reviewer("first"),
        decision="approve" if terminal == "approved" else "reject",
    )

    for decision in ("approve", "reject"):
        with pytest.raises(ApprovalNotPendingError):
            repository.record_decision(
                approval_id,
                reviewer=_reviewer(f"late-{decision}"),
                decision=decision,
            )

    status, version, rows = _state(engine, approval_id)
    assert (status, version, len(rows)) == (terminal, 2, 1)


def test_expired_request_refuses_decisions(
    repository: PostgresApprovalRepository,
    engine: Engine,
) -> None:
    now = datetime.now(UTC)
    approval_id = _create(
        repository,
        required_reviewers=1,
        created_at=now - timedelta(hours=2),
        expires_at=now - timedelta(hours=1),
    ).approval.approval_id

    for decision in ("approve", "reject"):
        with pytest.raises(ApprovalExpiredError):
            repository.record_decision(
                approval_id,
                reviewer=_reviewer(decision),
                decision=decision,
            )

    status, version, rows = _state(engine, approval_id)
    assert (status, version, rows) == ("pending", 1, [])


def test_unknown_approval_refused(repository: PostgresApprovalRepository) -> None:
    with pytest.raises(ApprovalRequestNotFoundError):
        repository.record_decision(str(uuid4()), reviewer=_reviewer(1), decision="approve")


def test_unsupported_decision_value_refused(repository: PostgresApprovalRepository) -> None:
    approval_id = _create(repository).approval.approval_id

    with pytest.raises(ValueError, match="Unsupported decision"):
        repository.record_decision(
            approval_id,
            reviewer=_reviewer(1),
            decision="abstain",  # type: ignore[arg-type]
        )


# --- Rollback, versioning and snapshots -------------------------------------------------


def test_failed_decision_insert_rolls_back(
    repository: PostgresApprovalRepository,
    engine: Engine,
) -> None:
    approval_id = _create(repository, required_reviewers=1).approval.approval_id
    oversized = verified_human(subject="reviewer-oversized", email="x" * 400 + "@example.test")

    with pytest.raises(ApprovalPersistenceError):
        repository.record_decision(approval_id, reviewer=oversized, decision="approve")

    assert _state(engine, approval_id) == ("pending", 1, [])
    outcome = repository.record_decision(approval_id, reviewer=_reviewer(1), decision="approve")
    assert outcome.status == "approved"


def test_failure_after_insert_and_transition_rolls_back_everything(
    repository: PostgresApprovalRepository,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approval_id = _create(repository, required_reviewers=1).approval.approval_id

    def explode(*_args: object, **_kwargs: object) -> DurableApprovalRecord:
        raise RuntimeError("simulated failure after insert and update")

    with monkeypatch.context() as patch:
        patch.setattr(postgres_repository, "_to_record", explode)
        with pytest.raises(RuntimeError, match="simulated failure"):
            repository.record_decision(approval_id, reviewer=_reviewer(1), decision="approve")

    assert _state(engine, approval_id) == ("pending", 1, [])


def test_snapshots_unchanged_by_decisions(repository: PostgresApprovalRepository) -> None:
    created = _create(repository, required_reviewers=2)
    approval_id = created.approval.approval_id

    repository.record_decision(approval_id, reviewer=_reviewer("a"), decision="approve")
    final = repository.record_decision(approval_id, reviewer=_reviewer("b"), decision="approve")

    record = final.record
    assert record.approval.payload == PAYMENT_PAYLOAD
    assert record.payload_hash == created.payload_hash == canonical_payload_hash(PAYMENT_PAYLOAD)
    assert record.policy_version == created.policy_version
    assert record.policy_snapshot == created.policy_snapshot
    assert record.approval.required_reviewers == created.approval.required_reviewers
    assert record.approval.created_at == created.approval.created_at
    assert record.approval.expires_at == created.approval.expires_at
    assert record.requested_by == created.requested_by


def test_decision_audit_attribution(repository: PostgresApprovalRepository) -> None:
    created = _create(repository, required_reviewers=1)
    correlation_id = str(uuid4())

    outcome = repository.record_decision(
        created.approval.approval_id,
        reviewer=_reviewer("audit"),
        decision="approve",
        comment="Matches signed invoice",
        correlation_id=correlation_id,
    )

    decision = outcome.decision
    assert decision.approval_id == created.approval.approval_id
    assert decision.payload_hash == created.payload_hash
    assert decision.decision == "approve"
    assert decision.reviewer_issuer == VERIFIED_TEST_ISSUER
    assert decision.reviewer_subject == "reviewer-audit"
    assert decision.reviewer_email == "reviewer-audit@example.test"
    assert decision.reviewer_display_name == "Reviewer audit"
    assert decision.reviewer_roles == ("viewer", "approver")
    assert decision.authentication_method == "oidc_jwks"
    assert decision.principal_type == "human"
    assert decision.comment == "Matches signed invoice"
    assert decision.correlation_id == correlation_id
    assert decision.decided_at.tzinfo is UTC
    assert created.approval.created_at <= decision.decided_at < created.approval.expires_at
    assert outcome.record.decisions == (decision,)
    assert repository.list_decisions(created.approval.approval_id) == (decision,)


# --- Reviewer identity enforcement ------------------------------------------------------


UNVERIFIED_REVIEWERS: dict[str, OperatorIdentity] = {
    "static-token": static_token_identity(roles=["approver", "admin"]),
    "oidc-development": OperatorIdentity(
        subject="dev-approver",
        display_name="Dev Approver",
        roles=["approver"],
        authentication_method="oidc_development",
        principal_type="human",
        issuer=VERIFIED_TEST_ISSUER,
    ),
    "auth-disabled": OperatorIdentity(
        subject="anonymous",
        display_name="Auth Disabled",
        roles=["admin"],
        authentication_method="auth_disabled",
    ),
    "jwks-unclassified": OperatorIdentity(
        subject="unclassified",
        display_name="Unclassified",
        roles=["approver"],
        authentication_method="oidc_jwks",
        principal_type="unknown",
        issuer=VERIFIED_TEST_ISSUER,
    ),
    "app-only": application_principal(),
    "mcp-agent-static": OperatorIdentity(
        subject="mcp_agent",
        display_name="MCP Agent",
        roles=["admin"],
        authentication_method="static_token",
    ),
    "mcp-agent-app": application_principal(subject="mcp_agent"),
    "missing-issuer": verified_human(subject="no-issuer", issuer=None),
    "blank-issuer": verified_human(subject="blank-issuer", issuer="   "),
    "untrusted-issuer": verified_human(subject="foreign", issuer="https://evil.example.test"),
    "viewer-role": verified_human(subject="viewer-only", roles=["viewer"]),
}


@pytest.mark.parametrize("identity_name", sorted(UNVERIFIED_REVIEWERS))
def test_ineligible_reviewers_refused_without_side_effects(
    repository: PostgresApprovalRepository,
    engine: Engine,
    identity_name: str,
) -> None:
    approval_id = _create(repository, required_reviewers=1).approval.approval_id

    for decision in ("approve", "reject"):
        with pytest.raises(ReviewerNotEligibleError):
            repository.record_decision(
                approval_id,
                reviewer=UNVERIFIED_REVIEWERS[identity_name],
                decision=decision,
            )

    assert _state(engine, approval_id) == ("pending", 1, [])


def test_repository_without_trusted_issuers_refuses_all_decisions(
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    repository = PostgresApprovalRepository(session_factory)
    approval_id = _create(repository).approval.approval_id

    with pytest.raises(ReviewerNotEligibleError, match="not trusted"):
        repository.record_decision(approval_id, reviewer=_reviewer(1), decision="approve")
    assert _state(engine, approval_id) == ("pending", 1, [])


def test_trusted_issuer_trailing_slash_is_normalized(
    session_factory: sessionmaker[Session],
) -> None:
    repository = PostgresApprovalRepository(
        session_factory, trusted_reviewer_issuers={VERIFIED_TEST_ISSUER + "/"}
    )
    approval_id = _create(repository).approval.approval_id

    outcome = repository.record_decision(approval_id, reviewer=_reviewer(1), decision="approve")
    assert outcome.status == "approved"


# --- Concurrency (independent engines, database row lock only) ---------------------------


DecisionCall = tuple[OperatorIdentity, str]


@pytest.fixture
def make_engine(postgres_url: str) -> Iterator[Callable[[], Engine]]:
    engines: list[Engine] = []

    def factory() -> Engine:
        database_engine = create_engine(postgres_url, pool_size=1, max_overflow=0)
        engines.append(database_engine)
        return database_engine

    yield factory
    for database_engine in engines:
        database_engine.dispose()


def _race(
    make_engine: Callable[[], Engine],
    lock_engine: Engine,
    approval_id: str,
    calls: list[DecisionCall],
) -> list[DecisionOutcome | BaseException]:
    """Run ``calls`` concurrently, each on its own engine, released together.

    A separate connection holds the approval row lock while every worker starts and
    blocks on ``SELECT ... FOR UPDATE``; releasing it forces maximal contention.
    """

    repositories = [
        PostgresApprovalRepository(
            sessionmaker(bind=make_engine(), autoflush=False, expire_on_commit=False),
            trusted_reviewer_issuers=TRUSTED_ISSUERS,
        )
        for _ in calls
    ]
    results: list[DecisionOutcome | BaseException] = [RuntimeError("not run")] * len(calls)
    ready = threading.Barrier(len(calls) + 1)

    def worker(index: int) -> None:
        identity, decision = calls[index]
        ready.wait()
        try:
            results[index] = repositories[index].record_decision(
                approval_id,
                reviewer=identity,
                decision=decision,  # type: ignore[arg-type]
            )
        except BaseException as exc:  # noqa: BLE001 - collected for assertions
            results[index] = exc

    with lock_engine.connect() as holder:
        transaction = holder.begin()
        holder.execute(
            text("SELECT 1 FROM approval_requests WHERE approval_id = :id FOR UPDATE"),
            {"id": approval_id},
        )
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(len(calls))]
        for thread in threads:
            thread.start()
        ready.wait()
        time.sleep(0.5)  # let every worker reach the row lock
        transaction.rollback()

    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "decision worker did not finish"
    return results


def _outcomes(results: list[DecisionOutcome | BaseException]) -> list[DecisionOutcome]:
    unexpected = [
        result
        for result in results
        if isinstance(result, BaseException)
        and not isinstance(result, ApprovalDecisionRejectedError)
    ]
    assert unexpected == [], f"unexpected errors: {unexpected!r}"
    return [result for result in results if isinstance(result, DecisionOutcome)]


@pytest.mark.parametrize(
    ("approvals", "rejections"),
    [(8, 0), (0, 8), (6, 2)],
    ids=["approve-only", "reject-only", "mixed"],
)
def test_concurrent_decisions_by_same_reviewer(
    repository: PostgresApprovalRepository,
    engine: Engine,
    make_engine: Callable[[], Engine],
    approvals: int,
    rejections: int,
) -> None:
    approval_id = _create(repository, required_reviewers=2).approval.approval_id
    same = _reviewer("same")
    calls: list[DecisionCall] = [(same, "approve")] * approvals + [(same, "reject")] * rejections

    results = _race(make_engine, engine, approval_id, calls)

    accepted = _outcomes(results)
    assert len(accepted) == 1
    # If the winning decision was an approval (quorum 2, still pending), every other
    # attempt is a duplicate; if a rejection won, the request is terminal and the status
    # check refuses the rest first. Either way exactly one decision is persisted.
    winner = accepted[0]
    expected_refusal: type[ApprovalDecisionRejectedError] = (
        DuplicateReviewerDecisionError
        if winner.decision.decision == "approve"
        else ApprovalNotPendingError
    )
    refused = [result for result in results if not isinstance(result, DecisionOutcome)]
    assert len(refused) == len(calls) - 1
    assert all(type(result) is expected_refusal for result in refused)
    _, version, rows = _state(engine, approval_id)
    assert len(rows) == 1 and version == 2
    _assert_consistent(engine, approval_id, 2)


def test_concurrent_decisions_by_distinct_reviewers(
    repository: PostgresApprovalRepository,
    engine: Engine,
    make_engine: Callable[[], Engine],
) -> None:
    approval_id = _create(repository, required_reviewers=3).approval.approval_id
    calls: list[DecisionCall] = [(_reviewer(f"d{i}"), "approve") for i in range(7)]

    results = _race(make_engine, engine, approval_id, calls)

    accepted = _outcomes(results)
    assert Counter(outcome.status for outcome in accepted) == {
        "partially_approved": 2,
        "approved": 1,
    }
    refused = [result for result in results if not isinstance(result, DecisionOutcome)]
    assert len(refused) == 4
    assert all(isinstance(result, ApprovalNotPendingError) for result in refused)
    assert sorted(outcome.record.version for outcome in accepted) == [2, 3, 4]
    assert _assert_consistent(engine, approval_id, 3) == "approved"


@pytest.mark.parametrize("round_number", range(3))
def test_approval_versus_rejection_race_single_reviewer(
    repository: PostgresApprovalRepository,
    engine: Engine,
    make_engine: Callable[[], Engine],
    round_number: int,
) -> None:
    approval_id = _create(repository, required_reviewers=1).approval.approval_id
    calls: list[DecisionCall] = []
    for i in range(4):
        calls.append((_reviewer(f"r{round_number}-approve-{i}"), "approve"))
        calls.append((_reviewer(f"r{round_number}-reject-{i}"), "reject"))

    results = _race(make_engine, engine, approval_id, calls)

    accepted = _outcomes(results)
    assert len(accepted) == 1
    winner = accepted[0]
    expected_status = "approved" if winner.decision.decision == "approve" else "rejected"
    assert winner.status == expected_status
    assert _assert_consistent(engine, approval_id, 1) == expected_status
    _, version, rows = _state(engine, approval_id)
    assert (version, len(rows)) == (2, 1)


@pytest.mark.parametrize("round_number", range(3))
def test_approval_versus_rejection_race_with_quorum(
    repository: PostgresApprovalRepository,
    engine: Engine,
    make_engine: Callable[[], Engine],
    round_number: int,
) -> None:
    approval_id = _create(repository, required_reviewers=2).approval.approval_id
    calls: list[DecisionCall] = [
        (_reviewer(f"q{round_number}-approve-{i}"), "approve") for i in range(3)
    ] + [(_reviewer(f"q{round_number}-reject-{i}"), "reject") for i in range(2)]

    results = _race(make_engine, engine, approval_id, calls)

    accepted = _outcomes(results)
    final_status = _assert_consistent(engine, approval_id, 2)
    assert final_status in {"approved", "rejected"}
    terminal = [outcome for outcome in accepted if outcome.status != "partially_approved"]
    assert len(terminal) == 1
    assert terminal[0].status == final_status
    _, version, rows = _state(engine, approval_id)
    assert len(accepted) == len(rows)
    assert version == 1 + len(rows)
