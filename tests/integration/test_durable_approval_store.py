"""PostgreSQL tests for shared, non-executing durable approval creation (Slice D4.1).

The MCP server and the HTTP operator API run as separate processes, each building its
own ``AppContainer``. These tests prove both see the same PostgreSQL approval state,
that the durable store stays isolated from the live in-memory decision/execution
workflow, and that nothing calls a payment provider.

Fixtures live in ``tests/integration/conftest.py``; these tests skip unless
``MPESA_TEST_POSTGRES_URL`` points at a local disposable ``*_test`` database.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from app.approvals.durable_store import DURABLE_APPROVAL_POLICY_VERSION, DurableApprovalStore
from app.approvals.integrity import canonical_payload_hash
from app.approvals.postgres_repository import (
    DuplicateApprovalRequestError,
    PostgresApprovalRepository,
)
from app.approvals.reviewer import ReviewerNotEligibleError
from app.auth.oidc import OperatorIdentity
from app.bootstrap.container import AppContainer
from app.config import Settings
from app.daraja.client import MockDarajaClient
from app.infrastructure.health import InfrastructureUnavailableError
from app.payments.providers import DarajaPaymentProvider
from app.policy.tool_policy import AGENT_FORBIDDEN_TOOLS, CURRENT_MCP_TOOLS, ToolPolicyEngine
from sqlalchemy import Engine, text
from tests.identity_fixtures import verified_human

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PAYMENT_PAYLOAD: dict[str, Any] = {
    "phone_number": "254700000000",
    "amount": 75_000,
    "account_reference": "INV-D41",
    "description": "Supplier settlement",
    "idempotency_key": "idem-d41",
}


def _settings(database_url: str, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "storage_mode": "postgres",
        "database_url": database_url,
        "approval_expiry_minutes": 45,
        "approval_required_reviewers": 1,
        "high_risk_approval_required_reviewers": 2,
        "high_risk_amount_threshold": 50_000,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def make_container(postgres_url: str) -> Iterator[Callable[..., AppContainer]]:
    """Build independent containers, each with its own engine and connection pool."""

    containers: list[AppContainer] = []

    def factory(**overrides: Any) -> AppContainer:
        container = AppContainer.mock(settings=_settings(postgres_url, **overrides))
        containers.append(container)
        return container

    yield factory
    for container in containers:
        _dispose(container)


@pytest.fixture
def no_payment_provider(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fail the test if any payment provider or Daraja STK call is attempted."""

    calls: list[str] = []

    def forbidden(name: str) -> Callable[..., Any]:
        def _call(*_args: object, **_kwargs: object) -> Any:
            calls.append(name)
            raise AssertionError(f"payment provider must not be called: {name}")

        return _call

    monkeypatch.setattr(
        DarajaPaymentProvider, "initiate_payment", forbidden("provider.initiate_payment")
    )
    monkeypatch.setattr(
        MockDarajaClient, "initiate_stk_push", forbidden("daraja.initiate_stk_push")
    )
    return calls


def _dispose(container: AppContainer) -> None:
    store = container.durable_approval_store
    if store is not None:
        repository = store._repository  # noqa: SLF001 - test cleanup only
        session_factory = repository._session_factory  # noqa: SLF001
        bind = getattr(session_factory, "kw", {}).get("bind")
        if bind is not None:
            bind.dispose()


def _store(container: AppContainer) -> DurableApprovalStore:
    assert container.durable_approval_store is not None
    return container.durable_approval_store


def _create(container: AppContainer, **overrides: Any) -> Any:
    values: dict[str, Any] = {
        "action": "initiate_stk_push",
        "payload": PAYMENT_PAYLOAD,
        "reason": "Amount exceeds the configured STK push limit.",
        "request_origin": "mcp_agent",
        "correlation_id": str(uuid4()),
    }
    values.update(overrides)
    return _store(container).create_approval_request(**values)


def _row_count(engine: Engine, approval_id: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text("SELECT count(*) FROM approval_requests WHERE approval_id = :id"),
                {"id": approval_id},
            ).scalar_one()
        )


def test_created_approval_is_persisted_with_snapshots(
    make_container: Callable[..., AppContainer],
    engine: Engine,
    no_payment_provider: list[str],
) -> None:
    container = make_container()
    correlation_id = str(uuid4())

    record = _create(container, correlation_id=correlation_id)

    approval = record.approval
    assert _row_count(engine, approval.approval_id) == 1
    assert approval.status == "pending"
    assert approval.payload == PAYMENT_PAYLOAD
    assert record.payload_hash == canonical_payload_hash(PAYMENT_PAYLOAD)
    assert record.requested_by == "mcp_agent"
    assert record.correlation_id == correlation_id
    # 75,000 >= the 50,000 high-risk threshold, so the high-risk quorum is snapshotted.
    assert approval.required_reviewers == 2
    assert approval.expires_at - approval.created_at == timedelta(minutes=45)
    assert record.policy_version == DURABLE_APPROVAL_POLICY_VERSION
    assert record.policy_snapshot == {
        "required_reviewers": 2,
        "base_required_reviewers": 1,
        "high_risk_required_reviewers": 2,
        "high_risk_amount_threshold": 50_000,
        "expiry_minutes": 45,
        "decision_authority": "verified_human_oidc",
        "minimum_reviewer_role": "approver",
    }
    assert record.version == 1
    assert record.decisions == ()
    assert no_payment_provider == []


def test_low_value_payload_uses_base_quorum(
    make_container: Callable[..., AppContainer],
) -> None:
    record = _create(make_container(), payload={**PAYMENT_PAYLOAD, "amount": 1_000})

    assert record.approval.required_reviewers == 1
    assert record.policy_snapshot["required_reviewers"] == 1


def test_operator_process_retrieves_approval_created_by_mcp_process(
    postgres_url: str,
    make_container: Callable[..., AppContainer],
) -> None:
    """Create in a separate Python process (MCP side); read here (operator API side)."""

    script = (
        "import json, sys\n"
        "from app.bootstrap.container import AppContainer\n"
        "from app.config import Settings\n"
        "settings = Settings(storage_mode='postgres', database_url=sys.argv[1],\n"
        "                    approval_expiry_minutes=45, high_risk_amount_threshold=50000,\n"
        "                    high_risk_approval_required_reviewers=2)\n"
        "store = AppContainer.mock(settings=settings).durable_approval_store\n"
        "record = store.create_approval_request(\n"
        "    action='initiate_stk_push', payload=json.loads(sys.argv[2]),\n"
        "    reason='Amount exceeds the configured STK push limit.',\n"
        "    request_origin='mcp_agent', correlation_id='corr-cross-process')\n"
        "print(json.dumps({'approval_id': record.approval.approval_id,\n"
        "                  'payload_hash': record.payload_hash}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, postgres_url, json.dumps(PAYMENT_PAYLOAD)],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    created = json.loads(completed.stdout.strip().splitlines()[-1])

    operator_container = make_container()
    record = _store(operator_container).get_approval_request(created["approval_id"])

    assert record is not None
    assert record.payload_hash == created["payload_hash"]
    assert record.approval.payload == PAYMENT_PAYLOAD
    assert record.approval.status == "pending"
    assert record.requested_by == "mcp_agent"
    assert record.correlation_id == "corr-cross-process"
    assert record.approval.required_reviewers == 2
    pending_ids = {item.approval.approval_id for item in _store(operator_container).list_requests()}
    assert created["approval_id"] in pending_ids


def test_approval_survives_application_reinitialization(
    make_container: Callable[..., AppContainer],
) -> None:
    first = make_container()
    record = _create(first)
    _dispose(first)

    restarted = make_container()

    assert _store(restarted).get_approval_request(record.approval.approval_id) == record


def test_duplicate_request_id_is_not_silently_created(
    make_container: Callable[..., AppContainer],
    engine: Engine,
) -> None:
    mcp_side, other_side = make_container(), make_container()
    approval_id = str(uuid4())
    original = _create(mcp_side, approval_id=approval_id)

    with pytest.raises(DuplicateApprovalRequestError):
        _create(
            other_side,
            approval_id=approval_id,
            payload={**PAYMENT_PAYLOAD, "amount": 1, "phone_number": "254799999999"},
        )

    assert _row_count(engine, approval_id) == 1
    assert _store(other_side).get_approval_request(approval_id) == original


def test_postgres_unavailable_fails_closed_without_memory_fallback(
    no_payment_provider: list[str],
) -> None:
    container = AppContainer.mock(
        settings=_settings(
            "postgresql+psycopg://nobody:nobody@127.0.0.1:1/unreachable_test?connect_timeout=2"
        )
    )
    try:
        with pytest.raises(InfrastructureUnavailableError):
            _create(container)
        with pytest.raises(InfrastructureUnavailableError):
            _store(container).get_approval_request(str(uuid4()))

        assert container.approval_repository.list_pending() == []
        assert no_payment_provider == []
    finally:
        _dispose(container)


def test_durable_approvals_are_isolated_from_live_decision_and_execution(
    make_container: Callable[..., AppContainer],
    engine: Engine,
    no_payment_provider: list[str],
) -> None:
    """The live in-memory workflow can neither see nor decide/execute a durable approval."""

    container = make_container()
    record = _create(container)
    approval_id = record.approval.approval_id

    assert container.approval_service.get_approval_request(approval_id) is None
    execution = container.payment_service.execute_approved_payment(
        approval_id, reviewer=verified_human()
    )
    rejection = container.approval_service.reject_request(approval_id, reviewer=verified_human())

    assert execution.status == "not_found"
    assert rejection.status == "not_found"
    assert no_payment_provider == []
    with engine.connect() as connection:
        status, version = connection.execute(
            text("SELECT status, version FROM approval_requests WHERE approval_id = :id"),
            {"id": approval_id},
        ).one()
        decisions = connection.execute(
            text("SELECT count(*) FROM approval_decisions WHERE approval_id = :id"),
            {"id": approval_id},
        ).scalar_one()
    assert (status, version, decisions) == ("pending", 1, 0)


def test_live_mcp_payment_path_does_not_write_durable_approvals(
    make_container: Callable[..., AppContainer],
    engine: Engine,
    no_payment_provider: list[str],
) -> None:
    container = make_container(max_stk_amount=10_000)
    with engine.connect() as connection:
        before = connection.execute(text("SELECT count(*) FROM approval_requests")).scalar_one()

    response = container.payment_service.initiate_stk_push(
        phone_number="254700000000",
        amount=75_000,
        account_reference="INV-LIVE",
        description="Above limit",
        request_origin="mcp_agent",
    )

    assert response.status == "approval_required"
    with engine.connect() as connection:
        after = connection.execute(text("SELECT count(*) FROM approval_requests")).scalar_one()
    assert after == before
    assert len(container.approval_repository.list_pending()) == 1
    assert no_payment_provider == []


def test_store_exposes_no_decision_operations() -> None:
    public = {name for name in vars(DurableApprovalStore) if not name.startswith("_")}

    assert public == {
        "from_settings",
        "create_approval_request",
        "get_approval_request",
        "list_requests",
    }


@pytest.mark.parametrize(
    "identity",
    [
        OperatorIdentity(
            subject="mcp_agent",
            display_name="MCP Agent",
            roles=["admin"],
            authentication_method="static_token",
        ),
        verified_human(subject="human-but-store-trusts-no-issuer"),
    ],
    ids=["mcp-agent", "verified-human"],
)
def test_store_repository_refuses_all_decisions(
    make_container: Callable[..., AppContainer],
    engine: Engine,
    identity: OperatorIdentity,
) -> None:
    container = make_container()
    approval_id = _create(container).approval.approval_id
    repository: PostgresApprovalRepository = _store(container)._repository  # noqa: SLF001

    for decision in ("approve", "reject"):
        with pytest.raises(ReviewerNotEligibleError):
            repository.record_decision(approval_id, reviewer=identity, decision=decision)

    with engine.connect() as connection:
        decisions = connection.execute(
            text("SELECT count(*) FROM approval_decisions WHERE approval_id = :id"),
            {"id": approval_id},
        ).scalar_one()
    assert decisions == 0


def test_mcp_agents_cannot_hold_decision_tools() -> None:
    policy = ToolPolicyEngine(enabled_tools={*CURRENT_MCP_TOOLS, *AGENT_FORBIDDEN_TOOLS})

    for tool in AGENT_FORBIDDEN_TOOLS:
        assert policy.evaluate(tool).allowed is False
    assert {"approve_payment_request", "reject_payment_request"} <= AGENT_FORBIDDEN_TOOLS


def test_mock_development_container_keeps_in_memory_workflow() -> None:
    container = AppContainer.mock()

    assert container.durable_approval_store is None
    approval = container.approval_service.create_approval_request(
        action="initiate_stk_push",
        payload=PAYMENT_PAYLOAD,
        reason="Development flow",
        request_origin="mcp_agent",
    )
    assert container.approval_service.get_approval_request(approval.approval_id) == approval
