"""Schema tests for durable approval persistence (migration 0008).

PostgreSQL-specific guarantees (JSONB, guard triggers, constraint SQLSTATEs) are only
asserted against a real PostgreSQL instance. Set ``MPESA_TEST_POSTGRES_URL`` to a
disposable local database whose name ends in ``_test``; otherwise those tests skip.
SQLite is never substituted for PostgreSQL-specific behaviour.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.config import get_settings
from app.storage.models import ApprovalDecisionModel, ApprovalRequestModel
from sqlalchemy import Engine, create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.exc import StaleDataError

POSTGRES_URL_ENV = "MPESA_TEST_POSTGRES_URL"
HEAD_REVISION = "0008_durable_approvals"
PREVIOUS_REVISION = "0007_audit_operator_roles"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

NOT_NULL_VIOLATION = "23502"
FOREIGN_KEY_VIOLATION = "23503"
UNIQUE_VIOLATION = "23505"
CHECK_VIOLATION = "23514"
RESTRICT_VIOLATION = "23001"

PAYMENT_PAYLOAD: dict[str, Any] = {
    "phone_number": "254700000000",
    "amount": 75_000,
    "account_reference": "INV-2026-001",
    "description": "Supplier settlement",
    "metadata": {"currency": "KES", "lines": [1, 2, 3]},
}
POLICY_SNAPSHOT: dict[str, Any] = {
    "required_reviewers": 2,
    "high_risk_amount_threshold": 50_000,
    "high_risk_required_reviewers": 2,
    "eligible_roles": ["approver", "admin"],
    "allow_development_reviewers": False,
}


def canonical_payload_hash(payload: dict[str, Any]) -> str:
    """Return the SHA-256 of the canonical JSON encoding of ``payload``."""

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _alembic_config(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    return Config("alembic.ini")


# --- Dialect-neutral migration round trip (runs everywhere, including CI) ---------------


def test_sqlite_migration_upgrade_and_downgrade_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'durable-approvals.sqlite'}"
    config = _alembic_config(database_url, monkeypatch)
    engine = create_engine(database_url)
    try:
        command.upgrade(config, "head")
        assert {"approval_requests", "approval_decisions"} <= set(inspect(engine).get_table_names())

        command.downgrade(config, PREVIOUS_REVISION)
        tables = set(inspect(engine).get_table_names())
        assert "approval_requests" not in tables
        assert "approval_decisions" not in tables
        assert "transactions" in tables

        command.upgrade(config, "head")
        assert "approval_decisions" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
        get_settings.cache_clear()


# --- PostgreSQL-specific guarantees -----------------------------------------------------


def _disposable_postgres_url() -> str:
    raw_url = os.environ.get(POSTGRES_URL_ENV, "").strip()
    if not raw_url:
        pytest.skip(f"{POSTGRES_URL_ENV} is not set; PostgreSQL schema tests skipped")

    url = make_url(raw_url)
    if not url.drivername.startswith("postgresql"):
        pytest.fail(f"{POSTGRES_URL_ENV} must be a PostgreSQL URL")
    if url.host not in LOCAL_HOSTS:
        pytest.fail(f"{POSTGRES_URL_ENV} must point at a local disposable instance")
    if not (url.database or "").endswith("_test"):
        pytest.fail(f"{POSTGRES_URL_ENV} database name must end with '_test'")
    return raw_url


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    database_url = _disposable_postgres_url()
    monkeypatch = pytest.MonkeyPatch()
    config = _alembic_config(database_url, monkeypatch)
    try:
        command.downgrade(config, "base")
        command.upgrade(config, "head")
        yield database_url
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


@pytest.fixture(scope="module")
def engine(postgres_url: str) -> Iterator[Engine]:
    database_engine = create_engine(postgres_url)
    yield database_engine
    database_engine.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _request(**overrides: Any) -> ApprovalRequestModel:
    now = datetime.now(UTC)
    values: dict[str, Any] = {
        "approval_id": str(uuid4()),
        "action": "initiate_payment",
        "status": "pending",
        "reason": "High-value supplier settlement",
        "payment_payload": PAYMENT_PAYLOAD,
        "payload_hash_algorithm": "sha256",
        "payload_hash": canonical_payload_hash(PAYMENT_PAYLOAD),
        "policy_version": "approval-policy/2026-11-23",
        "policy_snapshot": POLICY_SNAPSHOT,
        "required_reviewers": 2,
        "requested_by": "mcp-agent",
        "correlation_id": str(uuid4()),
        "created_at": now,
        "expires_at": now + timedelta(minutes=30),
    }
    values.update(overrides)
    return ApprovalRequestModel(**values)


def _decision(request: ApprovalRequestModel, **overrides: Any) -> ApprovalDecisionModel:
    values: dict[str, Any] = {
        "decision_id": str(uuid4()),
        "approval_id": request.approval_id,
        "payload_hash": request.payload_hash,
        "decision": "approve",
        "reviewer_issuer": "https://login.example.test/tenant/v2.0",
        "reviewer_subject": f"subject-{uuid4()}",
        "reviewer_email": "reviewer@example.test",
        "reviewer_display_name": "Reviewer One",
        "reviewer_roles": ["approver"],
        "authentication_method": "oidc_jwks",
        "principal_type": "human",
        "comment": "Verified against invoice",
        "correlation_id": str(uuid4()),
    }
    values.update(overrides)
    return ApprovalDecisionModel(**values)


def _commit(factory: sessionmaker[Session], *rows: object) -> None:
    with factory() as session:
        session.add_all(rows)
        session.commit()


def _assert_rejected(sqlstate: str, action: Callable[[], None]) -> None:
    with pytest.raises(IntegrityError) as raised:
        action()
    assert getattr(raised.value.orig, "sqlstate", None) == sqlstate


def _execute(engine: Engine, statement: str, **params: Any) -> None:
    with engine.begin() as connection:
        connection.execute(text(statement), params)


JSONB_REQUEST_COLUMNS = frozenset({"payment_payload", "policy_snapshot"})


def _raw_insert_request(engine: Engine, **overrides: Any) -> None:
    """Insert an approval request with raw SQL, bypassing ORM defaults and versioning.

    JSONB columns take JSON text; ``None`` binds SQL ``NULL``.
    """

    request = _request()
    columns: dict[str, Any] = {
        "approval_id": request.approval_id,
        "action": request.action,
        "status": request.status,
        "reason": request.reason,
        "payment_payload": json.dumps(PAYMENT_PAYLOAD),
        "payload_hash_algorithm": request.payload_hash_algorithm,
        "payload_hash": request.payload_hash,
        "policy_version": request.policy_version,
        "policy_snapshot": json.dumps(POLICY_SNAPSHOT),
        "required_reviewers": request.required_reviewers,
        "expires_at": request.expires_at,
        "version": 1,
        **overrides,
    }
    placeholders = ", ".join(
        f"CAST(:{name} AS jsonb)" if name in JSONB_REQUEST_COLUMNS else f":{name}"
        for name in columns
    )
    _execute(
        engine,
        f"INSERT INTO approval_requests ({', '.join(columns)}) VALUES ({placeholders})",
        **columns,
    )


def test_postgres_migration_upgrade_and_downgrade(
    postgres_url: str,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _alembic_config(postgres_url, monkeypatch)
    trigger_query = text(
        "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal "
        "AND tgrelid IN ('approval_requests'::regclass, 'approval_decisions'::regclass)"
    )
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == (
                HEAD_REVISION
            )
            triggers = set(connection.execute(trigger_query).scalars())
            column_type = connection.execute(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_name = 'approval_requests' AND column_name = 'payment_payload'"
                )
            ).scalar()
        assert triggers == {
            "trg_approval_requests_guard",
            "trg_approval_requests_no_truncate",
            "trg_approval_decisions_append_only",
            "trg_approval_decisions_no_truncate",
        }
        assert column_type == "jsonb"

        command.downgrade(config, PREVIOUS_REVISION)
        inspector = inspect(engine)
        assert "approval_requests" not in inspector.get_table_names()
        assert "approval_decisions" not in inspector.get_table_names()
        with engine.connect() as connection:
            remaining_functions = connection.execute(
                text(
                    "SELECT proname FROM pg_proc WHERE proname IN "
                    "('approval_requests_guard', 'approval_decisions_append_only')"
                )
            ).all()
            assert remaining_functions == []
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == (
                PREVIOUS_REVISION
            )
    finally:
        command.upgrade(config, "head")
        get_settings.cache_clear()

    assert {"approval_requests", "approval_decisions"} <= set(inspect(engine).get_table_names())


def test_approval_persists_after_commit(session_factory: sessionmaker[Session]) -> None:
    request = _request()
    decision = _decision(request)
    _commit(session_factory, request)
    _commit(session_factory, decision)

    with session_factory() as session:
        stored = session.get(ApprovalRequestModel, request.approval_id)
        stored_decision = session.get(ApprovalDecisionModel, decision.decision_id)

    assert stored is not None
    assert stored.status == "pending"
    assert stored.version == 1
    assert stored.required_reviewers == 2
    assert stored.resolved_at is None
    assert stored.expires_at > stored.created_at
    assert stored_decision is not None
    assert stored_decision.approval_id == request.approval_id
    assert stored_decision.reviewer_issuer == "https://login.example.test/tenant/v2.0"
    assert stored_decision.reviewer_roles == ["approver"]
    assert stored_decision.authentication_method == "oidc_jwks"
    assert stored_decision.principal_type == "human"
    assert stored_decision.decided_at is not None


def test_uncommitted_approval_is_not_persisted(session_factory: sessionmaker[Session]) -> None:
    request = _request()
    with session_factory() as session:
        session.add(request)
        session.flush()
        session.rollback()

    with session_factory() as session:
        assert session.get(ApprovalRequestModel, request.approval_id) is None


@pytest.mark.parametrize(
    "field",
    [
        "action",
        "status",
        "reason",
        "payment_payload",
        "payload_hash_algorithm",
        "payload_hash",
        "policy_version",
        "policy_snapshot",
        "required_reviewers",
        "expires_at",
    ],
)
def test_approval_request_required_fields(
    engine: Engine,
    field: str,
) -> None:
    _assert_rejected(NOT_NULL_VIOLATION, lambda: _raw_insert_request(engine, **{field: None}))


@pytest.mark.parametrize(
    "field",
    [
        "approval_id",
        "payload_hash",
        "decision",
        "reviewer_issuer",
        "reviewer_subject",
        "reviewer_roles",
        "authentication_method",
        "principal_type",
    ],
)
def test_approval_decision_required_fields(
    session_factory: sessionmaker[Session],
    field: str,
) -> None:
    request = _request()
    _commit(session_factory, request)

    _assert_rejected(
        NOT_NULL_VIOLATION,
        lambda: _commit(session_factory, _decision(request, **{field: None})),
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "approved_by_agent"},
        {"required_reviewers": 0},
        {"required_reviewers": 11},
        {"payload_hash_algorithm": "md5"},
        {"payload_hash": "ABCDEF" + "0" * 58},
        {"payload_hash": "0" * 63},
        {"action": ""},
        {"policy_version": ""},
        {"expires_at": datetime(2020, 1, 1, tzinfo=UTC)},
        {"status": "approved", "resolved_at": None},
        {"status": "pending", "resolved_at": datetime.now(UTC)},
    ],
    ids=lambda overrides: ",".join(f"{key}={value!r}"[:40] for key, value in overrides.items()),
)
def test_approval_request_check_constraints(
    session_factory: sessionmaker[Session],
    overrides: dict[str, Any],
) -> None:
    _assert_rejected(
        CHECK_VIOLATION,
        lambda: _commit(session_factory, _request(**overrides)),
    )


@pytest.mark.parametrize(
    ("overrides", "expected_sqlstate"),
    [
        ({"version": 0}, CHECK_VIOLATION),
        ({"payment_payload": "null"}, CHECK_VIOLATION),
        ({"payment_payload": '["not", "an", "object"]'}, CHECK_VIOLATION),
        ({"policy_snapshot": "null"}, CHECK_VIOLATION),
        ({"policy_snapshot": '"scalar"'}, CHECK_VIOLATION),
        ({"payment_payload": None}, NOT_NULL_VIOLATION),
    ],
    ids=lambda value: str(value)[:40],
)
def test_approval_request_raw_insert_constraints(
    engine: Engine,
    overrides: dict[str, Any],
    expected_sqlstate: str,
) -> None:
    _assert_rejected(expected_sqlstate, lambda: _raw_insert_request(engine, **overrides))


@pytest.mark.parametrize("roles", ["null", '"approver"', '{"role": "approver"}'])
def test_reviewer_roles_must_be_a_json_array(
    engine: Engine,
    session_factory: sessionmaker[Session],
    roles: str,
) -> None:
    request = _request()
    _commit(session_factory, request)

    _assert_rejected(
        CHECK_VIOLATION,
        lambda: _execute(
            engine,
            "INSERT INTO approval_decisions (decision_id, approval_id, payload_hash, decision,"
            " reviewer_issuer, reviewer_subject, reviewer_roles, authentication_method,"
            " principal_type) VALUES (:decision_id, :approval_id, :payload_hash, 'approve',"
            " 'https://idp.example.test', 'reviewer', CAST(:roles AS jsonb), 'oidc_jwks',"
            " 'human')",
            decision_id=str(uuid4()),
            approval_id=request.approval_id,
            payload_hash=request.payload_hash,
            roles=roles,
        ),
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"decision": "abstain"},
        {"reviewer_issuer": ""},
        {"reviewer_subject": ""},
        {"authentication_method": "agent_token"},
        {"principal_type": "service"},
    ],
    ids=lambda overrides: ",".join(f"{key}={value!r}" for key, value in overrides.items()),
)
def test_approval_decision_check_constraints(
    session_factory: sessionmaker[Session],
    overrides: dict[str, Any],
) -> None:
    request = _request()
    _commit(session_factory, request)

    _assert_rejected(
        CHECK_VIOLATION,
        lambda: _commit(session_factory, _decision(request, **overrides)),
    )


def test_duplicate_reviewer_decision_rejected(session_factory: sessionmaker[Session]) -> None:
    request = _request()
    _commit(session_factory, request)
    first = _decision(request, reviewer_subject="reviewer-a", decision="approve")
    _commit(session_factory, first)

    for decision in ("approve", "reject"):
        duplicate = _decision(request, reviewer_subject="reviewer-a", decision=decision)
        _assert_rejected(UNIQUE_VIOLATION, partial(_commit, session_factory, duplicate))

    with session_factory() as session:
        rows = session.scalars(
            select(ApprovalDecisionModel).where(
                ApprovalDecisionModel.approval_id == request.approval_id
            )
        ).all()
    assert [row.decision_id for row in rows] == [first.decision_id]


def test_same_subject_from_different_issuer_is_a_distinct_reviewer(
    session_factory: sessionmaker[Session],
) -> None:
    request = _request()
    _commit(session_factory, request)
    _commit(
        session_factory,
        _decision(request, reviewer_subject="shared-subject"),
        _decision(
            request,
            reviewer_subject="shared-subject",
            reviewer_issuer="https://other-idp.example.test",
        ),
    )

    with session_factory() as session:
        count = len(
            session.scalars(
                select(ApprovalDecisionModel).where(
                    ApprovalDecisionModel.approval_id == request.approval_id
                )
            ).all()
        )
    assert count == 2


def test_decision_for_unknown_approval_rejected(session_factory: sessionmaker[Session]) -> None:
    orphan = _request()
    _assert_rejected(
        FOREIGN_KEY_VIOLATION,
        lambda: _commit(session_factory, _decision(orphan)),
    )


def test_decision_bound_to_a_different_payload_hash_rejected(
    session_factory: sessionmaker[Session],
) -> None:
    request = _request()
    _commit(session_factory, request)
    tampered_hash = canonical_payload_hash({**PAYMENT_PAYLOAD, "amount": 1})

    _assert_rejected(
        FOREIGN_KEY_VIOLATION,
        lambda: _commit(session_factory, _decision(request, payload_hash=tampered_hash)),
    )


def test_approval_with_decisions_cannot_be_deleted(
    engine: Engine,
    session_factory: sessionmaker[Session],
) -> None:
    request = _request()
    _commit(session_factory, request)
    _commit(session_factory, _decision(request))

    _assert_rejected(
        RESTRICT_VIOLATION,
        lambda: _execute(
            engine,
            "DELETE FROM approval_requests WHERE approval_id = :approval_id",
            approval_id=request.approval_id,
        ),
    )


def test_payment_payload_snapshot_round_trips_with_matching_hash(
    session_factory: sessionmaker[Session],
) -> None:
    request = _request()
    _commit(session_factory, request)

    with session_factory() as session:
        stored = session.get(ApprovalRequestModel, request.approval_id)

    assert stored is not None
    assert stored.payment_payload == PAYMENT_PAYLOAD
    assert canonical_payload_hash(stored.payment_payload) == stored.payload_hash


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("payment_payload", json.dumps({**PAYMENT_PAYLOAD, "amount": 1})),
        ("payload_hash", "f" * 64),
        ("required_reviewers", 1),
        ("action", "initiate_stk_push"),
        ("expires_at", datetime(2099, 1, 1, tzinfo=UTC)),
        ("created_at", datetime(2021, 1, 1, tzinfo=UTC)),
        ("requested_by", "someone-else"),
    ],
)
def test_immutable_request_fields_cannot_change(
    engine: Engine,
    session_factory: sessionmaker[Session],
    column: str,
    value: object,
) -> None:
    request = _request()
    _commit(session_factory, request)

    _assert_rejected(
        RESTRICT_VIOLATION,
        lambda: _execute(
            engine,
            f"UPDATE approval_requests SET {column} = :value, version = version + 1 "
            "WHERE approval_id = :approval_id",
            value=value,
            approval_id=request.approval_id,
        ),
    )

    with session_factory() as session:
        stored = session.get(ApprovalRequestModel, request.approval_id)
    assert stored is not None
    assert stored.payment_payload == PAYMENT_PAYLOAD
    assert stored.payload_hash == canonical_payload_hash(PAYMENT_PAYLOAD)
    assert stored.version == 1


def test_policy_snapshot_persists_and_is_immutable(
    engine: Engine,
    session_factory: sessionmaker[Session],
) -> None:
    request = _request()
    _commit(session_factory, request)

    with session_factory() as session:
        stored = session.get(ApprovalRequestModel, request.approval_id)
    assert stored is not None
    assert stored.policy_version == "approval-policy/2026-11-23"
    assert stored.policy_snapshot == POLICY_SNAPSHOT
    assert stored.required_reviewers == POLICY_SNAPSHOT["required_reviewers"]

    for column, value in (
        ("policy_snapshot", json.dumps({**POLICY_SNAPSHOT, "required_reviewers": 1})),
        ("policy_version", "approval-policy/forged"),
    ):
        _assert_rejected(
            RESTRICT_VIOLATION,
            partial(
                _execute,
                engine,
                f"UPDATE approval_requests SET {column} = :value, version = version + 1 "
                "WHERE approval_id = :approval_id",
                value=value,
                approval_id=request.approval_id,
            ),
        )

    with session_factory() as session:
        unchanged = session.get(ApprovalRequestModel, request.approval_id)
    assert unchanged is not None
    assert unchanged.policy_snapshot == POLICY_SNAPSHOT


def test_resolution_advances_version_and_is_terminal(
    session_factory: sessionmaker[Session],
) -> None:
    request = _request()
    _commit(session_factory, request)

    with session_factory() as session:
        stored = session.get(ApprovalRequestModel, request.approval_id)
        assert stored is not None
        stored.status = "approved"
        stored.resolved_at = datetime.now(UTC)
        session.commit()
        assert stored.version == 2

    def reopen() -> None:
        with session_factory() as session:
            resolved = session.get(ApprovalRequestModel, request.approval_id)
            assert resolved is not None
            resolved.status = "pending"
            resolved.resolved_at = None
            session.commit()

    _assert_rejected(RESTRICT_VIOLATION, reopen)


def test_update_without_version_increment_rejected(
    engine: Engine,
    session_factory: sessionmaker[Session],
) -> None:
    request = _request()
    _commit(session_factory, request)

    _assert_rejected(
        RESTRICT_VIOLATION,
        lambda: _execute(
            engine,
            "UPDATE approval_requests SET status = 'rejected', resolved_at = now() "
            "WHERE approval_id = :approval_id",
            approval_id=request.approval_id,
        ),
    )


def test_concurrent_stale_update_detected(session_factory: sessionmaker[Session]) -> None:
    request = _request()
    _commit(session_factory, request)

    with session_factory() as first, session_factory() as second:
        first_view = first.get(ApprovalRequestModel, request.approval_id)
        second_view = second.get(ApprovalRequestModel, request.approval_id)
        assert first_view is not None
        assert second_view is not None

        first_view.status = "approved"
        first_view.resolved_at = datetime.now(UTC)
        first.commit()

        second_view.status = "rejected"
        second_view.resolved_at = datetime.now(UTC)
        with pytest.raises(StaleDataError):
            second.commit()

    with session_factory() as session:
        final = session.get(ApprovalRequestModel, request.approval_id)
    assert final is not None
    assert final.status == "approved"
    assert final.version == 2


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE approval_decisions SET decision = 'reject' WHERE decision_id = :decision_id",
        "UPDATE approval_decisions SET reviewer_subject = 'forged' "
        "WHERE decision_id = :decision_id",
        "DELETE FROM approval_decisions WHERE decision_id = :decision_id",
    ],
)
def test_decisions_are_append_only(
    engine: Engine,
    session_factory: sessionmaker[Session],
    statement: str,
) -> None:
    request = _request()
    decision = _decision(request)
    _commit(session_factory, request)
    _commit(session_factory, decision)

    _assert_rejected(
        RESTRICT_VIOLATION,
        lambda: _execute(engine, statement, decision_id=decision.decision_id),
    )

    with session_factory() as session:
        stored = session.get(ApprovalDecisionModel, decision.decision_id)
    assert stored is not None
    assert stored.decision == "approve"


@pytest.mark.parametrize("table", ["approval_requests", "approval_decisions"])
def test_truncate_rejected(engine: Engine, table: str) -> None:
    _assert_rejected(RESTRICT_VIOLATION, lambda: _execute(engine, f"TRUNCATE {table} CASCADE"))
