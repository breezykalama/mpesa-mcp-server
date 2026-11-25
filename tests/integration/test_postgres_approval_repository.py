"""PostgreSQL tests for the durable approval repository (Slice D2: create and read).

Fixtures live in ``tests/integration/conftest.py``; these tests skip unless
``MPESA_TEST_POSTGRES_URL`` points at a local disposable ``*_test`` database.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest
from app.approvals.integrity import (
    PAYLOAD_HASH_ALGORITHM,
    NonCanonicalPayloadError,
    canonical_payload_hash,
)
from app.approvals.postgres_repository import (
    ApprovalIntegrityError,
    ApprovalPersistenceError,
    ApprovalRequestNotFoundError,
    DuplicateApprovalRequestError,
    DurableApprovalRecord,
    PostgresApprovalRepository,
)
from app.infrastructure.health import InfrastructureUnavailableError
from app.storage.models import ApprovalDecisionModel
from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

PAYMENT_PAYLOAD: dict[str, Any] = {
    "phone_number": "254700000000",
    "amount": 75_000,
    "account_reference": "INV-2026-001",
    "description": "Supplier settlement — Nairobi 🇰🇪",
    "idempotency_key": "idem-0001",
    "metadata": {
        "lines": [1, 2, {"sku": "A-1", "qty": 3}],
        "flags": {"urgent": True, "manual": False},
        "note": None,
        "empty": "",
        "large": 2**62,
    },
}
POLICY_SNAPSHOT: dict[str, Any] = {
    "required_reviewers": 2,
    "high_risk_amount_threshold": 50_000,
    "high_risk_required_reviewers": 2,
    "eligible_roles": ["approver", "admin"],
    "allow_development_reviewers": False,
}
POLICY_VERSION = "approval-policy/2026-11-23"


@pytest.fixture
def repository(session_factory: sessionmaker[Session]) -> PostgresApprovalRepository:
    return PostgresApprovalRepository(session_factory)


@pytest.fixture
def independent_engine(postgres_url: str) -> Iterator[Engine]:
    """A second engine with its own connection pool, independent of ``engine``."""

    database_engine = create_engine(postgres_url)
    yield database_engine
    database_engine.dispose()


def _create(repository: PostgresApprovalRepository, **overrides: Any) -> DurableApprovalRecord:
    now = datetime.now(UTC)
    values: dict[str, Any] = {
        "action": "initiate_stk_push",
        "payload": PAYMENT_PAYLOAD,
        "reason": "High-value payment requires approval",
        "expires_at": now + timedelta(minutes=30),
        "required_reviewers": 2,
        "policy_version": POLICY_VERSION,
        "policy_snapshot": POLICY_SNAPSHOT,
        "requested_by": "mcp_agent",
        "correlation_id": str(uuid4()),
    }
    values.update(overrides)
    return repository.create(**values)


def _row_count(engine: Engine, approval_id: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text("SELECT count(*) FROM approval_requests WHERE approval_id = :approval_id"),
                {"approval_id": approval_id},
            ).scalar_one()
        )


def _add_decision(
    session_factory: sessionmaker[Session],
    record: DurableApprovalRecord,
    *,
    subject: str,
    decision: str,
    decided_at: datetime,
) -> str:
    decision_id = str(uuid4())
    with session_factory() as session:
        session.add(
            ApprovalDecisionModel(
                decision_id=decision_id,
                approval_id=record.approval.approval_id,
                payload_hash=record.payload_hash,
                decision=decision,
                reviewer_issuer="https://login.example.test/tenant/v2.0",
                reviewer_subject=subject,
                reviewer_email=f"{subject}@example.test",
                reviewer_display_name=subject.title(),
                reviewer_roles=["approver"],
                authentication_method="oidc_jwks",
                principal_type="human",
                decided_at=decided_at,
            )
        )
        session.commit()
    return decision_id


def _resolve(engine: Engine, approval_id: str, status: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE approval_requests SET status = :status, resolved_at = now(),"
                " version = version + 1 WHERE approval_id = :approval_id"
            ),
            {"status": status, "approval_id": approval_id},
        )


def test_create_and_get_approval_request(repository: PostgresApprovalRepository) -> None:
    created = _create(repository)
    fetched = repository.get(created.approval.approval_id)

    assert fetched == created
    approval = created.approval
    assert approval.status == "pending"
    assert approval.action == "initiate_stk_push"
    assert approval.required_reviewers == 2
    assert approval.reviewer_ids == []
    assert approval.review_count == 0
    assert approval.reviewed_at is None
    assert approval.expired_at is None
    assert created.payload_hash_algorithm == PAYLOAD_HASH_ALGORITHM
    assert created.payload_hash == canonical_payload_hash(PAYMENT_PAYLOAD)
    assert created.requested_by == "mcp_agent"
    assert created.version == 1
    assert created.resolved_at is None
    assert created.decisions == ()
    for timestamp in (approval.created_at, approval.expires_at):
        assert timestamp.tzinfo is UTC
    assert approval.expires_at > approval.created_at


def test_non_utc_aware_timestamps_are_normalized_to_utc(
    repository: PostgresApprovalRepository,
) -> None:
    nairobi = timezone(timedelta(hours=3))
    created_at = datetime(2026, 11, 23, 15, 0, tzinfo=nairobi)
    created = _create(
        repository,
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=30),
    )

    assert created.approval.created_at == datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
    assert created.approval.created_at.tzinfo is UTC


@pytest.mark.parametrize("field", ["created_at", "expires_at"])
def test_naive_timestamps_rejected_without_persisting(
    repository: PostgresApprovalRepository,
    engine: Engine,
    field: str,
) -> None:
    approval_id = str(uuid4())
    naive = datetime(2099, 1, 1, 12, 0)

    with pytest.raises(ValueError, match=field):
        _create(repository, approval_id=approval_id, **{field: naive})
    assert _row_count(engine, approval_id) == 0


def test_persists_across_independent_database_connections(
    repository: PostgresApprovalRepository,
    independent_engine: Engine,
) -> None:
    created = _create(repository)

    with independent_engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT status, payload_hash, payment_payload, policy_snapshot"
                    " FROM approval_requests WHERE approval_id = :approval_id"
                ),
                {"approval_id": created.approval.approval_id},
            )
            .mappings()
            .one()
        )

    assert row["status"] == "pending"
    assert row["payload_hash"] == created.payload_hash
    assert row["payment_payload"] == PAYMENT_PAYLOAD
    assert row["policy_snapshot"] == POLICY_SNAPSHOT


def test_available_after_repository_reinitialization(
    repository: PostgresApprovalRepository,
    independent_engine: Engine,
) -> None:
    created = _create(repository)

    reinitialized = PostgresApprovalRepository(
        sessionmaker(bind=independent_engine, autoflush=False, expire_on_commit=False)
    )

    assert reinitialized.get(created.approval.approval_id) == created


def test_duplicate_request_id_rejected_and_original_preserved(
    repository: PostgresApprovalRepository,
    engine: Engine,
) -> None:
    original = _create(repository)
    approval_id = original.approval.approval_id

    with pytest.raises(DuplicateApprovalRequestError):
        _create(
            repository,
            approval_id=approval_id,
            payload={**PAYMENT_PAYLOAD, "amount": 1, "phone_number": "254799999999"},
            reason="Overwrite attempt",
            required_reviewers=1,
            policy_snapshot={**POLICY_SNAPSHOT, "required_reviewers": 1},
        )

    assert _row_count(engine, approval_id) == 1
    assert repository.get(approval_id) == original


def test_payload_and_policy_snapshot_fidelity(repository: PostgresApprovalRepository) -> None:
    created = _create(repository)
    fetched = repository.get(created.approval.approval_id)

    assert fetched is not None
    assert fetched.approval.payload == PAYMENT_PAYLOAD
    assert fetched.policy_version == POLICY_VERSION
    assert fetched.policy_snapshot == POLICY_SNAPSHOT
    assert canonical_payload_hash(fetched.approval.payload) == fetched.payload_hash
    assert type(fetched.approval.payload["metadata"]["large"]) is int
    assert fetched.approval.payload["metadata"]["flags"]["urgent"] is True


@pytest.mark.parametrize(
    "payload",
    [
        {**PAYMENT_PAYLOAD, "amount": 75_000.0},
        {**PAYMENT_PAYLOAD, "amount": math.nan},
        {**PAYMENT_PAYLOAD, "lines": (1, 2)},
        {**PAYMENT_PAYLOAD, "metadata": {1: "non-string key"}},
        {**PAYMENT_PAYLOAD, "description": "nul\x00byte"},
        {**PAYMENT_PAYLOAD, "when": datetime(2026, 1, 1, tzinfo=UTC)},
    ],
    ids=["float", "nan", "tuple", "int-key", "nul", "datetime"],
)
def test_non_canonical_payload_rejected_without_persisting(
    repository: PostgresApprovalRepository,
    engine: Engine,
    payload: dict[str, Any],
) -> None:
    approval_id = str(uuid4())

    with pytest.raises(NonCanonicalPayloadError):
        _create(repository, approval_id=approval_id, payload=payload)
    assert _row_count(engine, approval_id) == 0


def test_list_by_status(repository: PostgresApprovalRepository, engine: Engine) -> None:
    first = _create(repository)
    second = _create(repository)
    resolved = _create(repository)
    _resolve(engine, resolved.approval.approval_id, "approved")

    pending_ids = [record.approval.approval_id for record in repository.list_by_status("pending")]
    approved = {
        record.approval.approval_id: record for record in repository.list_by_status("approved")
    }

    assert first.approval.approval_id in pending_ids
    assert second.approval.approval_id in pending_ids
    assert pending_ids.index(first.approval.approval_id) < pending_ids.index(
        second.approval.approval_id
    )
    assert resolved.approval.approval_id not in pending_ids
    approved_record = approved[resolved.approval.approval_id]
    assert approved_record.approval.status == "approved"
    assert approved_record.version == 2
    assert approved_record.resolved_at is not None
    assert approved_record.approval.reviewed_at == approved_record.resolved_at
    assert all(record.approval.status == "approved" for record in approved.values())


def test_list_by_unsupported_status_rejected(repository: PostgresApprovalRepository) -> None:
    with pytest.raises(ValueError, match="Unsupported approval status"):
        repository.list_by_status("approved_by_agent")  # type: ignore[arg-type]


def test_reads_associated_decisions_as_read_only_records(
    repository: PostgresApprovalRepository,
    session_factory: sessionmaker[Session],
) -> None:
    created = _create(repository)
    base = datetime.now(UTC)
    first_id = _add_decision(
        session_factory, created, subject="reviewer-a", decision="approve", decided_at=base
    )
    second_id = _add_decision(
        session_factory,
        created,
        subject="reviewer-b",
        decision="reject",
        decided_at=base + timedelta(seconds=5),
    )

    decisions = repository.list_decisions(created.approval.approval_id)
    fetched = repository.get(created.approval.approval_id)

    assert [decision.decision_id for decision in decisions] == [first_id, second_id]
    assert decisions[0].reviewer_subject == "reviewer-a"
    assert decisions[0].reviewer_issuer == "https://login.example.test/tenant/v2.0"
    assert decisions[0].reviewer_roles == ("approver",)
    assert decisions[0].payload_hash == created.payload_hash
    assert decisions[0].decided_at.tzinfo is UTC
    with pytest.raises(ValidationError):
        decisions[0].decision = "reject"

    assert fetched is not None
    assert fetched.decisions == decisions
    assert fetched.approval.reviewer_ids == ["reviewer-a"]
    assert fetched.approval.review_count == 1
    assert fetched.approval.status == "pending"
    assert fetched.approval.reviewed_at == decisions[-1].decided_at


def test_missing_approval_behavior(repository: PostgresApprovalRepository) -> None:
    missing_id = str(uuid4())

    assert repository.get(missing_id) is None
    with pytest.raises(ApprovalRequestNotFoundError):
        repository.list_decisions(missing_id)


def test_existing_approval_without_decisions_returns_empty_tuple(
    repository: PostgresApprovalRepository,
) -> None:
    created = _create(repository)

    assert repository.list_decisions(created.approval.approval_id) == ()


@pytest.mark.parametrize(
    "overrides",
    [
        {"required_reviewers": 0},
        {"required_reviewers": 11},
        {"policy_version": ""},
        {"action": ""},
        {"expires_at": datetime(2020, 1, 1, tzinfo=UTC)},
    ],
    ids=["quorum-zero", "quorum-eleven", "empty-policy-version", "empty-action", "expired"],
)
def test_failed_creation_rolls_back(
    repository: PostgresApprovalRepository,
    engine: Engine,
    overrides: dict[str, Any],
) -> None:
    approval_id = str(uuid4())

    with pytest.raises(ApprovalPersistenceError):
        _create(repository, approval_id=approval_id, **overrides)

    assert _row_count(engine, approval_id) == 0
    assert repository.get(approval_id) is None
    follow_up = _create(repository)
    assert repository.get(follow_up.approval.approval_id) == follow_up


def test_repository_exposes_no_generic_mutation_operations() -> None:
    public = {name for name in vars(PostgresApprovalRepository) if not name.startswith("_")}

    # record_decision (Slice D3) is the only state-changing operation besides create.
    assert public == {"create", "get", "list_by_status", "list_decisions", "record_decision"}


def test_returned_records_are_detached_from_storage(
    repository: PostgresApprovalRepository,
) -> None:
    created = _create(repository)

    with pytest.raises(ValidationError):
        created.payload_hash = "0" * 64
    created.approval.payload["amount"] = 1
    created.approval.status = "approved"
    created.policy_snapshot["required_reviewers"] = 1

    fetched = repository.get(created.approval.approval_id)
    assert fetched is not None
    assert fetched.approval.payload == PAYMENT_PAYLOAD
    assert fetched.approval.status == "pending"
    assert fetched.policy_snapshot == POLICY_SNAPSHOT
    assert fetched.version == 1


def test_tampered_payload_detected_on_read(
    repository: PostgresApprovalRepository,
    engine: Engine,
) -> None:
    created = _create(repository)
    approval_id = created.approval.approval_id

    # Simulate out-of-band tampering by a privileged actor who bypasses the guard trigger.
    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE approval_requests DISABLE TRIGGER trg_approval_requests_guard")
        )
        try:
            connection.execute(
                text(
                    "UPDATE approval_requests SET payment_payload = "
                    "jsonb_set(payment_payload, '{amount}', '1') WHERE approval_id = :approval_id"
                ),
                {"approval_id": approval_id},
            )
        finally:
            connection.execute(
                text("ALTER TABLE approval_requests ENABLE TRIGGER trg_approval_requests_guard")
            )

    try:
        with pytest.raises(ApprovalIntegrityError):
            repository.get(approval_id)
    finally:
        # Remove the tampered row so other tests that list pending approvals are unaffected.
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE approval_requests DISABLE TRIGGER trg_approval_requests_guard")
            )
            connection.execute(
                text("DELETE FROM approval_requests WHERE approval_id = :approval_id"),
                {"approval_id": approval_id},
            )
            connection.execute(
                text("ALTER TABLE approval_requests ENABLE TRIGGER trg_approval_requests_guard")
            )


def test_database_unavailable_maps_to_infrastructure_error() -> None:
    unreachable = create_engine(
        "postgresql+psycopg://nobody:nobody@127.0.0.1:1/unreachable_test",
        connect_args={"connect_timeout": 2},
    )
    repository = PostgresApprovalRepository(sessionmaker(bind=unreachable))
    try:
        with pytest.raises(InfrastructureUnavailableError):
            repository.get(str(uuid4()))
        with pytest.raises(InfrastructureUnavailableError):
            _create(repository)
    finally:
        unreachable.dispose()


@pytest.mark.parametrize("supplied_id", [False, True], ids=["generated-id", "supplied-id"])
def test_create_with_default_expiring_sessionmaker(
    engine: Engine,
    independent_engine: Engine,
    supplied_id: bool,
) -> None:
    """Regression: a default sessionmaker (expire_on_commit=True) must not break create().

    Reading the committed ORM instance after its session closed raised
    DetachedInstanceError even though the row had been persisted.
    """

    default_factory = sessionmaker(bind=engine)
    assert default_factory.kw.get("expire_on_commit", True) is True
    repository = PostgresApprovalRepository(default_factory)
    overrides = {"approval_id": str(uuid4())} if supplied_id else {}

    created = _create(repository, **overrides)
    approval_id = created.approval.approval_id

    if supplied_id:
        assert approval_id == overrides["approval_id"]
    assert _row_count(engine, approval_id) == 1
    with engine.connect() as connection:
        stored_id = connection.execute(
            text("SELECT approval_id FROM approval_requests WHERE approval_id = :approval_id"),
            {"approval_id": approval_id},
        ).scalar_one()
    assert stored_id == approval_id
    assert created.approval.payload == PAYMENT_PAYLOAD
    assert created.payload_hash == canonical_payload_hash(PAYMENT_PAYLOAD)

    fresh = PostgresApprovalRepository(sessionmaker(bind=independent_engine))
    assert fresh.get(approval_id) == created
    assert approval_id in {
        record.approval.approval_id for record in fresh.list_by_status("pending")
    }
    assert fresh.list_decisions(approval_id) == ()
