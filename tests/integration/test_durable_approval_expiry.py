"""PostgreSQL tests for atomic durable approval expiry transitions (Slice D4.3).

Expiry moves ``pending`` requests whose database deadline has passed to ``expired`` in
one conditional ``UPDATE ... RETURNING`` over rows locked ``FOR UPDATE`` (targeted) or
``FOR UPDATE SKIP LOCKED`` (batch). These tests prove the transition is idempotent, safe
across independent workers, mutually exclusive with D3 decisions, preserves snapshots
and decision history, and never touches a payment provider.

Concurrency tests run every worker on its own engine (independent connections and
pools). Fixtures live in ``tests/integration/conftest.py``; these tests skip unless
``MPESA_TEST_POSTGRES_URL`` points at a local disposable ``*_test`` database.
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from app.approvals.durable_store import DurableApprovalStore
from app.approvals.postgres_repository import (
    MAX_EXPIRY_BATCH_SIZE,
    ApprovalDecisionRejectedError,
    ApprovalExpiredError,
    ApprovalNotPendingError,
    ApprovalRequestNotFoundError,
    DecisionOutcome,
    DurableApprovalRecord,
    ExpiryOutcome,
    PostgresApprovalRepository,
)
from app.bootstrap.container import AppContainer
from app.callbacks.routes import get_app_container
from app.config import Settings
from app.daraja.client import MockDarajaClient
from app.infrastructure.health import InfrastructureUnavailableError
from app.main import app
from app.payments.providers import DarajaPaymentProvider
from app.policy.tool_policy import AGENT_FORBIDDEN_TOOLS, CURRENT_MCP_TOOLS
from app.services.payment_service import PaymentService
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker
from tests.identity_fixtures import VERIFIED_TEST_ISSUER, verified_human

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TRUSTED_ISSUERS = frozenset({VERIFIED_TEST_ISSUER})
PAYMENT_PAYLOAD: dict[str, Any] = {
    "phone_number": "254700000000",
    "amount": 75_000,
    "account_reference": "INV-D43",
    "description": "Supplier settlement",
    "idempotency_key": "idem-d43",
}
POLICY_SNAPSHOT: dict[str, Any] = {
    "required_reviewers": 1,
    "decision_authority": "verified_human_oidc",
    "minimum_reviewer_role": "approver",
}


@pytest.fixture(autouse=True)
def no_payment_provider(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fail the test if expiry reaches any payment provider or execution path."""

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
def repository(session_factory: sessionmaker[Session]) -> PostgresApprovalRepository:
    return PostgresApprovalRepository(session_factory, trusted_reviewer_issuers=TRUSTED_ISSUERS)


@pytest.fixture
def make_repository(postgres_url: str) -> Iterator[Callable[[], PostgresApprovalRepository]]:
    """Build repositories on independent engines (separate connection pools)."""

    engines: list[Engine] = []

    def factory() -> PostgresApprovalRepository:
        database_engine = create_engine(postgres_url, pool_size=2)
        engines.append(database_engine)
        return PostgresApprovalRepository(
            sessionmaker(bind=database_engine, autoflush=False, expire_on_commit=False),
            trusted_reviewer_issuers=TRUSTED_ISSUERS,
        )

    yield factory
    for database_engine in engines:
        database_engine.dispose()


def _database_now(engine: Engine) -> datetime:
    with engine.connect() as connection:
        value: datetime = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
    return value


def _create(
    repository: PostgresApprovalRepository,
    engine: Engine,
    *,
    expires_in: timedelta,
    required_reviewers: int = 1,
) -> DurableApprovalRecord:
    """Create a request whose deadline is ``expires_in`` from the database clock."""

    now = _database_now(engine)
    return repository.create(
        action="initiate_stk_push",
        payload=PAYMENT_PAYLOAD,
        reason="High-value payment requires approval",
        created_at=now - timedelta(hours=2),
        expires_at=now + expires_in,
        required_reviewers=required_reviewers,
        policy_version="approval-policy/v1",
        policy_snapshot={**POLICY_SNAPSHOT, "required_reviewers": required_reviewers},
        requested_by="mcp_agent",
        correlation_id="corr-d43",
    )


def _overdue(repository: PostgresApprovalRepository, engine: Engine, **kwargs: Any) -> str:
    record = _create(repository, engine, expires_in=-timedelta(minutes=5), **kwargs)
    return record.approval.approval_id


def _wait_until(engine: Engine, deadline: datetime, *, extra: float = 0.0) -> None:
    remaining = (deadline - _database_now(engine)).total_seconds() + extra
    if remaining > 0:
        time.sleep(remaining)


def _state(engine: Engine, approval_id: str) -> tuple[str, int, int]:
    with engine.connect() as connection:
        status, version = connection.execute(
            text("SELECT status, version FROM approval_requests WHERE approval_id = :id"),
            {"id": approval_id},
        ).one()
        decisions = connection.execute(
            text("SELECT count(*) FROM approval_decisions WHERE approval_id = :id"),
            {"id": approval_id},
        ).scalar_one()
    return status, version, int(decisions)


def _drain(repository: PostgresApprovalRepository) -> None:
    """Expire overdue rows left by earlier tests so batch assertions are exact."""

    while repository.expire_due_requests(limit=MAX_EXPIRY_BATCH_SIZE):
        pass


def _run_concurrently(
    calls: list[Callable[[], Any]],
    *,
    lock_engine: Engine | None = None,
    approval_id: str | None = None,
    release: Callable[[], None] = lambda: time.sleep(0.5),
) -> list[Any]:
    """Start ``calls`` together; optionally hold the row lock until ``release`` returns."""

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
        with lock_engine.connect() as holder:
            transaction = holder.begin()
            holder.execute(
                text("SELECT 1 FROM approval_requests WHERE approval_id = :id FOR UPDATE"),
                {"id": approval_id},
            )
            for thread in threads:
                thread.start()
            ready.wait()
            release()
            transaction.rollback()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "worker did not finish"
    return results


# --- Single-request transitions --------------------------------------------------------


def test_pending_request_expires_after_its_deadline(
    repository: PostgresApprovalRepository,
    engine: Engine,
) -> None:
    approval_id = _overdue(repository, engine)

    outcome = repository.expire_request(approval_id)

    assert outcome.expired is True
    approval = outcome.record.approval
    assert approval.status == "expired"
    assert approval.expired_at is not None
    assert outcome.record.resolved_at == approval.expired_at
    assert approval.expired_at >= approval.expires_at
    assert outcome.record.version == 2
    assert _state(engine, approval_id) == ("expired", 2, 0)


def test_request_before_its_deadline_remains_pending(
    repository: PostgresApprovalRepository,
    engine: Engine,
) -> None:
    approval_id = _create(repository, engine, expires_in=timedelta(minutes=30)).approval.approval_id

    outcome = repository.expire_request(approval_id)

    assert outcome.expired is False
    assert outcome.record.approval.status == "pending"
    assert outcome.record.approval.expired_at is None
    assert approval_id not in repository.expire_due_requests(limit=MAX_EXPIRY_BATCH_SIZE)
    assert _state(engine, approval_id) == ("pending", 1, 0)


def test_approved_and_rejected_requests_are_never_expired(
    repository: PostgresApprovalRepository,
    engine: Engine,
) -> None:
    approved = _create(repository, engine, expires_in=timedelta(seconds=1))
    rejected = _create(repository, engine, expires_in=timedelta(seconds=1))
    repository.record_decision(
        approved.approval.approval_id, reviewer=verified_human("a-1"), decision="approve"
    )
    repository.record_decision(
        rejected.approval.approval_id, reviewer=verified_human("r-1"), decision="reject"
    )
    _wait_until(engine, approved.approval.expires_at, extra=0.2)
    _wait_until(engine, rejected.approval.expires_at, extra=0.2)

    batch = repository.expire_due_requests(limit=MAX_EXPIRY_BATCH_SIZE)
    for record, status in ((approved, "approved"), (rejected, "rejected")):
        approval_id = record.approval.approval_id
        outcome = repository.expire_request(approval_id)
        assert approval_id not in batch
        assert outcome.expired is False
        assert outcome.record.approval.status == status
        assert outcome.record.approval.expired_at is None
        assert _state(engine, approval_id) == (status, 2, 1)


def test_repeated_expiry_attempts_are_idempotent(
    repository: PostgresApprovalRepository,
    engine: Engine,
) -> None:
    approval_id = _overdue(repository, engine)

    first = repository.expire_request(approval_id)
    repeats = [repository.expire_request(approval_id) for _ in range(3)]
    batch = repository.expire_due_requests(limit=MAX_EXPIRY_BATCH_SIZE)

    assert first.expired is True
    assert [outcome.expired for outcome in repeats] == [False, False, False]
    assert all(outcome.record == first.record for outcome in repeats)
    assert approval_id not in batch
    assert _state(engine, approval_id) == ("expired", 2, 0)


def test_expired_request_refuses_decisions_and_cannot_return_to_pending(
    repository: PostgresApprovalRepository,
    engine: Engine,
) -> None:
    approval_id = _overdue(repository, engine)
    repository.expire_request(approval_id)

    for index, decision in enumerate(("approve", "reject")):
        with pytest.raises(ApprovalNotPendingError):
            repository.record_decision(
                approval_id,
                reviewer=verified_human(f"late-{index}"),
                decision=decision,  # type: ignore[arg-type]
            )
    with pytest.raises(DBAPIError, match="resolved and cannot change"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE approval_requests SET status = 'pending', resolved_at = NULL, "
                    "version = version + 1 WHERE approval_id = :id"
                ),
                {"id": approval_id},
            )
    assert _state(engine, approval_id) == ("expired", 2, 0)


def test_expiry_preserves_snapshots_and_decision_history(
    repository: PostgresApprovalRepository,
    engine: Engine,
) -> None:
    created = _create(repository, engine, expires_in=timedelta(seconds=1), required_reviewers=2)
    approval_id = created.approval.approval_id
    partial = repository.record_decision(
        approval_id, reviewer=verified_human("first-reviewer"), decision="approve"
    )
    assert partial.status == "partially_approved"
    _wait_until(engine, created.approval.expires_at, extra=0.2)

    outcome = repository.expire_request(approval_id)

    assert outcome.expired is True
    record = outcome.record
    assert record.approval.status == "expired"
    assert record.version == 3
    assert record.approval.payload == created.approval.payload == PAYMENT_PAYLOAD
    assert record.payload_hash == created.payload_hash
    assert record.payload_hash_algorithm == created.payload_hash_algorithm
    assert record.policy_version == created.policy_version
    assert record.policy_snapshot == created.policy_snapshot
    assert record.approval.required_reviewers == 2
    assert record.approval.created_at == created.approval.created_at
    assert record.approval.expires_at == created.approval.expires_at
    assert record.requested_by == created.requested_by
    assert record.correlation_id == created.correlation_id
    # The partial approval is kept verbatim; expiry adds no decision row.
    assert record.decisions == partial.record.decisions
    assert record.decisions[0].reviewer_issuer == VERIFIED_TEST_ISSUER
    assert record.decisions[0].reviewer_subject == "first-reviewer"
    assert record.approval.reviewer_ids == ["first-reviewer"]
    assert _state(engine, approval_id) == ("expired", 3, 1)


def test_unknown_request_raises_not_found(repository: PostgresApprovalRepository) -> None:
    with pytest.raises(ApprovalRequestNotFoundError):
        repository.expire_request("00000000-0000-0000-0000-000000000000")


@pytest.mark.parametrize("limit", [0, -1, MAX_EXPIRY_BATCH_SIZE + 1, True])
def test_batch_size_is_bounded(repository: PostgresApprovalRepository, limit: int) -> None:
    with pytest.raises(ValueError, match="limit must be between"):
        repository.expire_due_requests(limit=limit)


def test_batch_expires_only_due_requests_up_to_the_limit(
    repository: PostgresApprovalRepository,
    engine: Engine,
) -> None:
    _drain(repository)
    due = [_overdue(repository, engine) for _ in range(5)]
    future = _create(repository, engine, expires_in=timedelta(minutes=30)).approval.approval_id

    first = repository.expire_due_requests(limit=3)
    second = repository.expire_due_requests(limit=3)
    third = repository.expire_due_requests(limit=3)

    assert len(first) == 3 and len(second) == 2 and third == ()
    assert sorted(first + second) == sorted(due)
    assert all(_state(engine, approval_id) == ("expired", 2, 0) for approval_id in due)
    assert _state(engine, future) == ("pending", 1, 0)


# --- Concurrency ----------------------------------------------------------------------


def test_independent_workers_racing_to_expire_one_request_transition_once(
    repository: PostgresApprovalRepository,
    engine: Engine,
    make_repository: Callable[[], PostgresApprovalRepository],
) -> None:
    approval_id = _overdue(repository, engine)
    workers = [make_repository() for _ in range(8)]

    results = _run_concurrently(
        [partial(worker.expire_request, approval_id) for worker in workers],
        lock_engine=engine,
        approval_id=approval_id,
    )

    assert all(isinstance(result, ExpiryOutcome) for result in results), results
    assert Counter(result.expired for result in results) == {True: 1, False: 7}
    assert {result.record.approval.status for result in results} == {"expired"}
    assert _state(engine, approval_id) == ("expired", 2, 0)


def test_concurrent_batch_workers_expire_each_request_exactly_once(
    repository: PostgresApprovalRepository,
    engine: Engine,
    make_repository: Callable[[], PostgresApprovalRepository],
) -> None:
    _drain(repository)
    due = {_overdue(repository, engine) for _ in range(30)}
    workers = [make_repository() for _ in range(6)]

    results = _run_concurrently(
        [partial(worker.expire_due_requests, limit=10) for worker in workers]
    )

    assert all(isinstance(result, tuple) for result in results), results
    claimed = [approval_id for result in results for approval_id in result]
    claimed.extend(repository.expire_due_requests(limit=MAX_EXPIRY_BATCH_SIZE))
    assert len(claimed) == len(set(claimed)), "a request was expired more than once"
    assert set(claimed) == due
    assert all(_state(engine, approval_id) == ("expired", 2, 0) for approval_id in due)


def test_batch_skips_a_request_locked_by_an_in_flight_decision(
    repository: PostgresApprovalRepository,
    engine: Engine,
) -> None:
    _drain(repository)
    locked = _overdue(repository, engine)
    free = _overdue(repository, engine)

    with engine.connect() as holder:
        transaction = holder.begin()
        holder.execute(
            text("SELECT 1 FROM approval_requests WHERE approval_id = :id FOR UPDATE"),
            {"id": locked},
        )
        started = time.monotonic()
        expired_while_locked = repository.expire_due_requests(limit=MAX_EXPIRY_BATCH_SIZE)
        elapsed = time.monotonic() - started
        transaction.rollback()

    assert expired_while_locked == (free,)
    assert elapsed < 2.0, "batch expiry must not wait on locked rows"
    assert _state(engine, locked) == ("pending", 1, 0)
    assert repository.expire_due_requests(limit=MAX_EXPIRY_BATCH_SIZE) == (locked,)
    assert _state(engine, locked) == ("expired", 2, 0)


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_decision_versus_expiry_after_the_deadline_always_expires(
    repository: PostgresApprovalRepository,
    engine: Engine,
    make_repository: Callable[[], PostgresApprovalRepository],
    decision: str,
) -> None:
    """Both workers wait on the row lock past the deadline; only expiry may succeed."""

    record = _create(repository, engine, expires_in=timedelta(seconds=1))
    approval_id = record.approval.approval_id
    _wait_until(engine, record.approval.expires_at, extra=0.1)
    decider, expirer = make_repository(), make_repository()

    results = _run_concurrently(
        [
            lambda: decider.record_decision(
                approval_id,
                reviewer=verified_human("racing-reviewer"),
                decision=decision,  # type: ignore[arg-type]
            ),
            lambda: expirer.expire_request(approval_id),
        ],
        lock_engine=engine,
        approval_id=approval_id,
    )

    decision_result, expiry_result = results
    assert isinstance(decision_result, (ApprovalExpiredError, ApprovalNotPendingError))
    assert isinstance(expiry_result, ExpiryOutcome) and expiry_result.expired is True
    assert _state(engine, approval_id) == ("expired", 2, 0)


@pytest.mark.parametrize("release_offset", [-0.15, 0.0, 0.15])
@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_decision_versus_expiry_at_the_deadline_has_one_terminal_outcome(
    repository: PostgresApprovalRepository,
    engine: Engine,
    make_repository: Callable[[], PostgresApprovalRepository],
    decision: str,
    release_offset: float,
) -> None:
    """Released around the deadline: exactly one of decision or expiry wins, never both."""

    record = _create(repository, engine, expires_in=timedelta(seconds=1.5))
    approval_id = record.approval.approval_id
    deadline = record.approval.expires_at
    decider = make_repository()
    expirers = [make_repository() for _ in range(3)]

    def expire_repeatedly(worker: PostgresApprovalRepository) -> list[ExpiryOutcome]:
        outcomes = []
        stop_at = time.monotonic() + 3.0
        while time.monotonic() < stop_at:
            outcomes.append(worker.expire_request(approval_id))
            if outcomes[-1].record.approval.status != "pending":
                break
            time.sleep(0.02)
        return outcomes

    results = _run_concurrently(
        [
            lambda: decider.record_decision(
                approval_id,
                reviewer=verified_human("deadline-reviewer"),
                decision=decision,  # type: ignore[arg-type]
            ),
            *[partial(expire_repeatedly, worker) for worker in expirers],
        ],
        lock_engine=engine,
        approval_id=approval_id,
        release=lambda: _wait_until(engine, deadline, extra=release_offset),
    )
    _wait_until(engine, deadline, extra=0.1)
    final_attempt = repository.expire_request(approval_id)

    decision_result = results[0]
    assert all(isinstance(result, list) for result in results[1:]), results
    expiry_outcomes = [outcome for result in results[1:] for outcome in result]
    expiry_outcomes.append(final_attempt)
    transitions = sum(outcome.expired for outcome in expiry_outcomes)
    status, version, decisions = _state(engine, approval_id)
    if isinstance(decision_result, DecisionOutcome):
        expected = "approved" if decision == "approve" else "rejected"
        assert (status, version, decisions, transitions) == (expected, 2, 1, 0)
    else:
        assert isinstance(decision_result, ApprovalDecisionRejectedError), decision_result
        assert (status, version, decisions) == ("expired", 2, 0)
        assert transitions == 1


# --- Reads, isolation and failure ------------------------------------------------------


def test_operator_reads_reflect_persisted_expiry(
    postgres_url: str,
    engine: Engine,
    repository: PostgresApprovalRepository,
) -> None:
    container = AppContainer.mock(
        settings=Settings(
            storage_mode="postgres",
            database_url=postgres_url,
            operator_auth_enabled=False,
        )
    )
    store = container.durable_approval_store
    assert isinstance(store, DurableApprovalStore)
    expired_id = _overdue(repository, engine)
    overdue_id = _overdue(repository, engine)
    assert store.expire_request(expired_id).expired is True

    app.dependency_overrides[get_app_container] = lambda: container
    try:
        client = TestClient(app)
        expired_body = client.get(f"/durable-approvals/{expired_id}").json()["approval"]
        overdue_body = client.get(f"/durable-approvals/{overdue_id}").json()["approval"]
    finally:
        app.dependency_overrides.clear()
        bind = getattr(store._repository._session_factory, "kw", {}).get("bind")  # noqa: SLF001
        if bind is not None:
            bind.dispose()

    assert expired_body["approval"]["status"] == "expired"
    assert expired_body["approval"]["expired_at"] is not None
    assert expired_body["version"] == 2
    # Reads never rewrite state: an overdue request stays persisted as pending until an
    # expiry transition runs.
    assert overdue_body["approval"]["status"] == "pending"
    assert overdue_body["approval"]["expired_at"] is None
    assert _state(engine, overdue_id) == ("pending", 1, 0)
    assert expired_id in {r.approval.approval_id for r in store.list_requests("expired")}
    assert expired_id not in {r.approval.approval_id for r in store.list_requests("pending")}


def test_store_batch_expiry_returns_the_number_expired(
    repository: PostgresApprovalRepository,
    engine: Engine,
    session_factory: sessionmaker[Session],
) -> None:
    _drain(repository)
    store = DurableApprovalStore.from_settings(Settings(), session_factory)
    due = [_overdue(repository, engine) for _ in range(3)]

    assert store.expire_due_requests(limit=2) == 2
    assert store.expire_due_requests() == 1
    assert store.expire_due_requests() == 0
    assert all(_state(engine, approval_id)[0] == "expired" for approval_id in due)


def test_postgres_unavailable_fails_closed() -> None:
    unreachable = create_engine(
        "postgresql+psycopg://ci_test:ci_test@127.0.0.1:1/mpesa_ci_test?connect_timeout=2"
    )
    try:
        repository = PostgresApprovalRepository(sessionmaker(bind=unreachable))
        store = DurableApprovalStore.from_settings(Settings(), sessionmaker(bind=unreachable))
        with pytest.raises(InfrastructureUnavailableError):
            repository.expire_request("any-id")
        with pytest.raises(InfrastructureUnavailableError):
            repository.expire_due_requests()
        with pytest.raises(InfrastructureUnavailableError):
            store.expire_due_requests()
    finally:
        unreachable.dispose()


def test_expiry_is_not_an_mcp_capability() -> None:
    tool_names = {*CURRENT_MCP_TOOLS, *AGENT_FORBIDDEN_TOOLS}
    assert not any("expire" in name or "durable" in name for name in tool_names)
    for source in (REPOSITORY_ROOT / "app" / "mcp").rglob("*.py"):
        content = source.read_text(encoding="utf-8")
        assert "durable_approval" not in content, source
        assert "expire_due_requests" not in content, source
        assert "expire_request" not in content, source


def test_expiry_never_calls_a_payment_provider(
    repository: PostgresApprovalRepository,
    engine: Engine,
    no_payment_provider: list[str],
) -> None:
    approval_id = _overdue(repository, engine)

    repository.expire_request(approval_id)
    repository.expire_due_requests()

    assert no_payment_provider == []
