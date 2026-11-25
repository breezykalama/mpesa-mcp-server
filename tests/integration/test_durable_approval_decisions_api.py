"""PostgreSQL tests for isolated, non-executing durable human decisions (Slice D4.2).

HTTP tests use real RS256 JWTs verified by ``JWKSOIDCIdentityProvider`` against an
in-process mock discovery/JWKS endpoint, so the reviewer identity, issuer and
verified-human status all come from the server-side authentication dependency.

Every test that records or attempts a decision runs with failing stubs on the payment
provider, the Daraja STK call and the live in-memory approval/execution methods.

Fixtures live in ``tests/integration/conftest.py``; these tests skip unless
``MPESA_TEST_POSTGRES_URL`` points at a local disposable ``*_test`` database.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from app.approvals.durable_decisions import (
    DurableApprovalDecisionService,
    trusted_reviewer_issuers_from_settings,
)
from app.approvals.durable_store import DurableApprovalStore
from app.approvals.reviewer import ReviewerNotEligibleError
from app.approvals.service import ApprovalService
from app.auth import security
from app.auth.oidc import JWKSOIDCIdentityProvider, OperatorIdentity
from app.bootstrap.container import AppContainer
from app.callbacks.routes import get_app_container
from app.config import Settings
from app.config_validation import APPROVAL_PERSISTENCE_ISSUE, collect_config_issues
from app.daraja.client import MockDarajaClient
from app.main import app
from app.payments.providers import DarajaPaymentProvider
from app.services.payment_service import PaymentService
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from tests.identity_fixtures import (
    application_principal,
    development_identity,
    static_token_identity,
    verified_human,
)
from tests.unit.test_oidc_jwks_provider import AUDIENCE, ISSUER, create_key_pair, create_token
from tests.unit.test_production_config_gate import HARDENED_PRODUCTION

SIGNING_KEY = create_key_pair(kid="durable-decision-key")
DELEGATED: dict[str, object] = {"scp": "operator.access"}
APPROVER_TOKEN = "durable-static-approver-token"
HIGH_RISK_PAYLOAD: dict[str, Any] = {
    "phone_number": "254700000000",
    "amount": 75_000,
    "account_reference": "INV-D42-HIGH",
    "description": "Supplier settlement",
    "idempotency_key": "idem-d42-high",
}
LOW_RISK_PAYLOAD: dict[str, Any] = {**HIGH_RISK_PAYLOAD, "amount": 20_000}
OIDC_SETTINGS: dict[str, Any] = {
    "auth_mode": "oidc",
    "oidc_provider_mode": "jwks",
    "oidc_issuer": ISSUER,
    "oidc_audience": AUDIENCE,
    "oidc_viewer_groups": "finance_viewers",
    "oidc_approver_groups": "finance_approvers",
    "oidc_admin_groups": "finance_admins",
}


@pytest.fixture(autouse=True)
def mock_jwks_endpoint(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={"jwks_uri": f"{ISSUER}/keys"})
        if request.url.path == "/keys":
            return httpx.Response(200, json={"keys": [SIGNING_KEY["jwk"]]})
        return httpx.Response(404)

    def provider_factory(settings: Settings) -> JWKSOIDCIdentityProvider:
        return JWKSOIDCIdentityProvider(
            settings,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

    monkeypatch.setattr(security, "JWKSOIDCIdentityProvider", provider_factory)
    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def no_execution(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fail on any payment provider, STK push or live in-memory decision/execution call."""

    calls: list[str] = []

    def forbidden(name: str) -> Callable[..., Any]:
        def _call(*_args: object, **_kwargs: object) -> Any:
            calls.append(name)
            raise AssertionError(f"must not be called from durable decisions: {name}")

        return _call

    targets: tuple[tuple[type, str], ...] = (
        (DarajaPaymentProvider, "initiate_payment"),
        (MockDarajaClient, "initiate_stk_push"),
        (PaymentService, "execute_approved_payment"),
        (PaymentService, "initiate_stk_push"),
        (ApprovalService, "approve_request"),
        (ApprovalService, "reject_request"),
    )
    for owner, attribute in targets:
        monkeypatch.setattr(owner, attribute, forbidden(f"{owner.__name__}.{attribute}"))
    return calls


@pytest.fixture
def make_container(postgres_url: str) -> Iterator[Callable[..., AppContainer]]:
    """Build independent API instances, each with its own engine and connection pool."""

    containers: list[AppContainer] = []

    def factory(**overrides: Any) -> AppContainer:
        values: dict[str, Any] = {
            "storage_mode": "postgres",
            "database_url": postgres_url,
            "approval_expiry_minutes": 45,
            "approval_required_reviewers": 1,
            "high_risk_approval_required_reviewers": 2,
            "high_risk_amount_threshold": 50_000,
            **OIDC_SETTINGS,
        }
        values.update(overrides)
        container = AppContainer.mock(settings=Settings(**values))
        containers.append(container)
        return container

    yield factory
    for container in containers:
        store = container.durable_approval_store
        if store is not None:
            bind = getattr(store._repository._session_factory, "kw", {}).get("bind")  # noqa: SLF001
            if bind is not None:
                bind.dispose()


def _store(container: AppContainer) -> DurableApprovalStore:
    assert container.durable_approval_store is not None
    return container.durable_approval_store


def _create(container: AppContainer, payload: dict[str, Any] = LOW_RISK_PAYLOAD) -> str:
    record = _store(container).create_approval_request(
        action="initiate_stk_push",
        payload=payload,
        reason="Amount exceeds the configured STK push limit.",
        request_origin="mcp_agent",
        correlation_id=str(uuid4()),
    )
    return record.approval.approval_id


def _token(subject: str, *, groups: list[str] | None = None, **claims: object) -> str:
    extra: dict[str, object] = {
        "sub": subject,
        "email": f"{subject}@example.test",
        "name": subject.replace("-", " ").title(),
        **DELEGATED,
    }
    extra.update(claims)
    issuer = extra.pop("iss", ISSUER)
    return create_token(
        key_pair=SIGNING_KEY,
        groups=groups or ["finance_approvers"],
        issuer=str(issuer),
        extra_claims=extra,
    )


def _call(
    container: AppContainer,
    method: str,
    path: str,
    *,
    token: str | None = None,
    json: dict[str, Any] | None = None,
) -> httpx.Response:
    app.dependency_overrides[get_app_container] = lambda: container
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return TestClient(app).request(method, path, headers=headers, json=json)


def _decide(
    container: AppContainer,
    approval_id: str,
    decision: str,
    token: str | None,
    json: dict[str, Any] | None = None,
) -> httpx.Response:
    return _call(
        container, "POST", f"/durable-approvals/{approval_id}/{decision}", token=token, json=json
    )


def _state(engine: Engine, approval_id: str) -> tuple[str, int, list[tuple[Any, ...]]]:
    with engine.connect() as connection:
        status, version = connection.execute(
            text("SELECT status, version FROM approval_requests WHERE approval_id = :id"),
            {"id": approval_id},
        ).one()
        rows = connection.execute(
            text(
                "SELECT decision, reviewer_issuer, reviewer_subject, comment "
                "FROM approval_decisions WHERE approval_id = :id ORDER BY decided_at"
            ),
            {"id": approval_id},
        ).all()
    return status, version, [tuple(row) for row in rows]


# --- Verified human decisions ---------------------------------------------------------


def test_verified_human_approves_durable_request_without_execution(
    make_container: Callable[..., AppContainer],
    engine: Engine,
    no_execution: list[str],
) -> None:
    container = make_container()
    approval_id = _create(container)
    before = _store(container).get_approval_request(approval_id)
    assert before is not None

    response = _decide(
        container, approval_id, "approve", _token("human-1"), json={"comment": "Verified."}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "approved"
    assert body["payment_execution"] == "not_performed"
    assert body["approval"]["approval"]["status"] == "approved"
    assert body["approval"]["version"] == 2
    assert body["decision"]["decision"] == "approve"
    assert body["decision"]["reviewer_issuer"] == ISSUER
    assert body["decision"]["reviewer_subject"] == "human-1"
    assert body["decision"]["authentication_method"] == "oidc_jwks"
    assert body["decision"]["principal_type"] == "human"
    assert body["decision"]["comment"] == "Verified."
    assert _state(engine, approval_id) == (
        "approved",
        2,
        [("approve", ISSUER, "human-1", "Verified.")],
    )
    after = _store(container).get_approval_request(approval_id)
    assert after is not None
    assert after.approval.payload == before.approval.payload == LOW_RISK_PAYLOAD
    assert after.payload_hash == before.payload_hash
    assert after.policy_snapshot == before.policy_snapshot
    assert after.policy_version == before.policy_version
    assert no_execution == []


def test_verified_human_rejects_durable_request_without_provider_call(
    make_container: Callable[..., AppContainer],
    engine: Engine,
    no_execution: list[str],
) -> None:
    container = make_container()
    approval_id = _create(container, HIGH_RISK_PAYLOAD)

    response = _decide(container, approval_id, "reject", _token("human-1"))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "rejected"
    assert body["payment_execution"] == "not_performed"
    assert body["decision"]["decision"] == "reject"
    assert _state(engine, approval_id) == ("rejected", 2, [("reject", ISSUER, "human-1", None)])
    assert no_execution == []


def test_two_distinct_humans_on_independent_instances_reach_quorum(
    make_container: Callable[..., AppContainer],
    engine: Engine,
    no_execution: list[str],
) -> None:
    """Instance A records the first approval; instance B the second; both see the result."""

    instance_a = make_container()
    instance_b = make_container()
    approval_id = _create(instance_a, HIGH_RISK_PAYLOAD)

    first = _decide(instance_a, approval_id, "approve", _token("human-1"))
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "partially_approved"
    assert first.json()["approval"]["approval"]["status"] == "pending"

    seen_by_b = _call(instance_b, "GET", f"/durable-approvals/{approval_id}", token=_token("h-3"))
    assert seen_by_b.status_code == 200
    assert seen_by_b.json()["approval"]["approval"]["review_count"] == 1

    second = _decide(instance_b, approval_id, "approve", _token("human-2"))
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "approved"
    assert second.json()["payment_execution"] == "not_performed"

    seen_by_a = _call(instance_a, "GET", f"/durable-approvals/{approval_id}", token=_token("h-3"))
    approval = seen_by_a.json()["approval"]
    assert approval["approval"]["status"] == "approved"
    assert approval["approval"]["reviewer_ids"] == ["human-1", "human-2"]
    assert approval["version"] == 3
    assert [d["reviewer_subject"] for d in approval["decisions"]] == ["human-1", "human-2"]
    assert no_execution == []


@pytest.mark.parametrize("second_decision", ["approve", "reject"])
def test_duplicate_reviewer_decision_is_rejected(
    make_container: Callable[..., AppContainer],
    engine: Engine,
    second_decision: str,
) -> None:
    container = make_container()
    approval_id = _create(container, HIGH_RISK_PAYLOAD)
    token = _token("human-1")
    assert _decide(container, approval_id, "approve", token).status_code == 200

    response = _decide(container, approval_id, second_decision, token)

    assert response.status_code == 409
    assert response.json()["status"] == "duplicate_decision"
    assert _state(engine, approval_id) == ("pending", 2, [("approve", ISSUER, "human-1", None)])


@pytest.mark.parametrize("first_decision", ["approve", "reject"])
def test_terminal_request_refuses_further_decisions(
    make_container: Callable[..., AppContainer],
    engine: Engine,
    first_decision: str,
) -> None:
    container = make_container()
    approval_id = _create(container)
    assert _decide(container, approval_id, first_decision, _token("human-1")).status_code == 200
    terminal = "approved" if first_decision == "approve" else "rejected"

    for decision in ("approve", "reject"):
        response = _decide(container, approval_id, decision, _token(f"other-{decision}"))
        assert response.status_code == 409
        assert response.json()["status"] == "not_pending"

    status, version, rows = _state(engine, approval_id)
    assert (status, version, len(rows)) == (terminal, 2, 1)


def test_expired_request_refuses_decisions(
    make_container: Callable[..., AppContainer],
    engine: Engine,
) -> None:
    container = make_container()
    now = datetime.now(UTC)
    approval_id = (
        _store(container)
        ._repository.create(  # noqa: SLF001 - backdated creation for the expiry case
            action="initiate_stk_push",
            payload=LOW_RISK_PAYLOAD,
            reason="Expired durable request",
            created_at=now - timedelta(hours=2),
            expires_at=now - timedelta(hours=1),
            required_reviewers=1,
            policy_version="approval-policy/v1",
            policy_snapshot={"required_reviewers": 1},
            requested_by="mcp_agent",
        )
        .approval.approval_id
    )

    for decision in ("approve", "reject"):
        response = _decide(container, approval_id, decision, _token(f"human-{decision}"))
        assert response.status_code == 409
        assert response.json()["status"] == "expired"

    assert _state(engine, approval_id) == ("pending", 1, [])


def test_unknown_durable_approval_returns_not_found(
    make_container: Callable[..., AppContainer],
) -> None:
    container = make_container()
    missing = str(uuid4())

    assert _decide(container, missing, "approve", _token("human-1")).status_code == 404
    assert (
        _call(
            container, "GET", f"/durable-approvals/{missing}", token=_token("human-1")
        ).status_code
        == 404
    )


# --- Ineligible identities -------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "credential"),
    [
        (
            {"auth_mode": "token", "operator_approver_token": APPROVER_TOKEN},
            APPROVER_TOKEN,
        ),
        (
            {"oidc_provider_mode": "development", "oidc_development_groups": "finance_approvers"},
            "any-development-credential",
        ),
        ({"operator_auth_enabled": False}, None),
    ],
    ids=["static-token", "development-oidc", "auth-disabled"],
)
@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_non_jwks_identities_are_denied(
    make_container: Callable[..., AppContainer],
    engine: Engine,
    overrides: dict[str, Any],
    credential: str | None,
    decision: str,
) -> None:
    container = make_container(**overrides)
    approval_id = _create(container)

    response = _decide(container, approval_id, decision, credential)

    assert response.status_code == 403
    assert response.json()["status"] == "reviewer_not_authorized"
    assert _state(engine, approval_id) == ("pending", 1, [])


@pytest.mark.parametrize(
    "claims",
    [{"idtyp": "app", "scp": None}, {"scp": None}],
    ids=["app-only", "unknown-principal"],
)
@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_app_only_and_unknown_principals_are_denied(
    make_container: Callable[..., AppContainer],
    engine: Engine,
    claims: dict[str, object],
    decision: str,
) -> None:
    container = make_container()
    approval_id = _create(container)
    token_claims = {key: value for key, value in claims.items() if value is not None}
    extra = {"sub": "service-principal", **token_claims}
    token = create_token(key_pair=SIGNING_KEY, groups=["finance_admins"], extra_claims=extra)

    response = _decide(container, approval_id, decision, token)

    assert response.status_code == 403
    assert response.json()["status"] == "reviewer_not_authorized"
    assert _state(engine, approval_id) == ("pending", 1, [])


def test_viewer_role_is_denied(
    make_container: Callable[..., AppContainer],
    engine: Engine,
) -> None:
    container = make_container()
    approval_id = _create(container)

    response = _decide(
        container, approval_id, "approve", _token("viewer-1", groups=["finance_viewers"])
    )

    assert response.status_code == 403
    assert _state(engine, approval_id) == ("pending", 1, [])


@pytest.mark.parametrize(
    "token_factory",
    [
        lambda: _token("human-1", iss="https://untrusted.example.test"),
        lambda: create_token(
            key_pair=create_key_pair(kid="durable-decision-key"),
            groups=["finance_approvers"],
            extra_claims={"sub": "human-1", **DELEGATED},
        ),
        lambda: None,
    ],
    ids=["untrusted-issuer", "foreign-signing-key", "missing-token"],
)
def test_unverifiable_tokens_are_rejected_before_decisions(
    make_container: Callable[..., AppContainer],
    engine: Engine,
    token_factory: Callable[[], str | None],
) -> None:
    container = make_container()
    approval_id = _create(container)

    response = _decide(container, approval_id, "approve", token_factory())

    assert response.status_code == 401
    assert _state(engine, approval_id) == ("pending", 1, [])


@pytest.mark.parametrize(
    "spoofed",
    [
        {"reviewer_subject": "someone-else"},
        {"reviewer_issuer": "https://untrusted.example.test"},
        {"roles": ["admin"]},
        {"principal_type": "human", "authentication_method": "oidc_jwks"},
        {"reviewer": {"subject": "human-2", "issuer": ISSUER}},
    ],
    ids=["subject", "issuer", "roles", "verified-human", "reviewer-object"],
)
def test_reviewer_identity_cannot_be_supplied_in_request_body(
    make_container: Callable[..., AppContainer],
    engine: Engine,
    spoofed: dict[str, Any],
) -> None:
    container = make_container()
    approval_id = _create(container)

    # An unauthenticated caller with spoofed fields is still refused outright.
    unauthenticated = _decide(container, approval_id, "approve", None, json=spoofed)
    assert unauthenticated.status_code == 401

    # An authenticated caller cannot override their identity: unknown fields are 422.
    response = _decide(container, approval_id, "approve", _token("human-1"), json=spoofed)
    assert response.status_code == 422
    assert _state(engine, approval_id) == ("pending", 1, [])


def test_static_token_caller_cannot_claim_verified_identity_through_body(
    make_container: Callable[..., AppContainer],
    engine: Engine,
) -> None:
    container = make_container(auth_mode="token", operator_approver_token=APPROVER_TOKEN)
    approval_id = _create(container)

    response = _decide(
        container,
        approval_id,
        "approve",
        APPROVER_TOKEN,
        json={"authentication_method": "oidc_jwks", "issuer": ISSUER},
    )

    assert response.status_code == 422
    assert _decide(container, approval_id, "approve", APPROVER_TOKEN).status_code == 403
    assert _state(engine, approval_id) == ("pending", 1, [])


# --- Service layer --------------------------------------------------------------------


@pytest.mark.parametrize(
    "identity",
    [
        OperatorIdentity(
            subject="mcp_agent",
            display_name="MCP Agent",
            roles=["admin"],
            authentication_method="static_token",
        ),
        static_token_identity(),
        development_identity(),
        application_principal(),
        verified_human(issuer=None),
        verified_human(issuer="   "),
        verified_human(issuer="https://untrusted.example.test"),
        verified_human(roles=["viewer"]),
    ],
    ids=[
        "mcp-agent",
        "static-token",
        "development",
        "app-only",
        "missing-issuer",
        "blank-issuer",
        "untrusted-issuer",
        "viewer-role",
    ],
)
def test_decision_service_refuses_ineligible_identities(
    make_container: Callable[..., AppContainer],
    engine: Engine,
    identity: OperatorIdentity,
) -> None:
    container = make_container()
    service = container.durable_approval_decisions
    assert service is not None
    approval_id = _create(container)

    with pytest.raises(ReviewerNotEligibleError):
        service.approve(approval_id, reviewer=identity)
    with pytest.raises(ReviewerNotEligibleError):
        service.reject(approval_id, reviewer=identity)

    assert _state(engine, approval_id) == ("pending", 1, [])


def test_decision_service_trusts_only_the_configured_jwks_issuer(
    make_container: Callable[..., AppContainer],
    engine: Engine,
) -> None:
    container = make_container()
    service = container.durable_approval_decisions
    assert service is not None
    approval_id = _create(container, HIGH_RISK_PAYLOAD)

    outcome = service.approve(approval_id, reviewer=verified_human("h-1", issuer=ISSUER))

    assert outcome.status == "partially_approved"
    assert outcome.decision.reviewer_issuer == ISSUER


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, frozenset({ISSUER})),
        ({"oidc_issuer": f"{ISSUER}/"}, frozenset({f"{ISSUER}/"})),
        ({"oidc_provider_mode": "development"}, frozenset()),
        ({"auth_mode": "token"}, frozenset()),
        ({"operator_auth_enabled": False}, frozenset()),
        ({"oidc_issuer": None}, frozenset()),
        ({"oidc_issuer": "  "}, frozenset()),
    ],
    ids=[
        "jwks",
        "jwks-trailing-slash",
        "development",
        "token",
        "auth-disabled",
        "no-issuer",
        "blank",
    ],
)
def test_trusted_issuers_come_only_from_validated_jwks_configuration(
    overrides: dict[str, Any],
    expected: frozenset[str],
) -> None:
    settings = Settings(**{**OIDC_SETTINGS, **overrides})

    assert trusted_reviewer_issuers_from_settings(settings) == expected


def test_trailing_slash_issuer_matches_verified_token_issuer(
    make_container: Callable[..., AppContainer],
) -> None:
    """The JWKS provider verifies ``iss`` without the slash; the trust set normalizes it."""

    container = make_container(oidc_issuer=f"{ISSUER}/")
    approval_id = _create(container)

    response = _decide(container, approval_id, "approve", _token("human-1"))

    assert response.status_code == 200, response.text
    assert response.json()["decision"]["reviewer_issuer"] == ISSUER


def test_decision_service_exposes_only_decision_operations() -> None:
    """No execute, claim or payment operation exists on the durable decision service."""

    assert {name for name in vars(DurableApprovalDecisionService) if not name.startswith("_")} == {
        "from_settings",
        "approve",
        "reject",
    }


# --- Isolation ------------------------------------------------------------------------


def test_durable_routes_fail_closed_without_postgres(no_execution: list[str]) -> None:
    container = AppContainer.mock(settings=Settings(**OIDC_SETTINGS))
    assert container.durable_approval_store is None
    assert container.durable_approval_decisions is None
    approval_id = str(uuid4())

    for method, path in (
        ("GET", f"/durable-approvals/{approval_id}"),
        ("POST", f"/durable-approvals/{approval_id}/approve"),
        ("POST", f"/durable-approvals/{approval_id}/reject"),
    ):
        response = _call(container, method, path, token=_token("human-1"))
        assert response.status_code == 503
        assert response.json()["status"] == "durable_approvals_unavailable"
    assert no_execution == []


def test_no_route_executes_payments_from_durable_approvals() -> None:
    durable_routes = {
        (method, getattr(route, "path", ""))
        for route in app.routes
        if getattr(route, "path", "").startswith("/durable-approvals")
        for method in getattr(route, "methods", set())
    }

    assert durable_routes == {
        ("GET", "/durable-approvals/{approval_id}"),
        ("POST", "/durable-approvals/{approval_id}/approve"),
        ("POST", "/durable-approvals/{approval_id}/reject"),
    }


def test_durable_decision_modules_do_not_reference_payment_execution() -> None:
    import app.approvals.durable_decisions as decisions_module
    import app.approvals.durable_routes as routes_module

    forbidden_modules = ("app.payments", "app.daraja", "app.services", "app.approvals.service")
    forbidden_names = {
        "PaymentService",
        "ApprovalService",
        "payment_service",
        "approval_service",
        "execute_approved_payment",
        "approve_request",
        "reject_request",
        "initiate_stk_push",
        "initiate_payment",
    }
    for module in (decisions_module, routes_module):
        tree = ast.parse(Path(module.__file__ or "").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(forbidden_modules), node.module
                assert not {alias.name for alias in node.names} & forbidden_names
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(forbidden_modules), alias.name
            elif isinstance(node, ast.Name):
                assert node.id not in forbidden_names, (module.__name__, node.id)
            elif isinstance(node, ast.Attribute):
                assert node.attr not in forbidden_names, (module.__name__, node.attr)


def test_live_in_memory_routes_do_not_see_durable_approvals(
    make_container: Callable[..., AppContainer],
    engine: Engine,
    no_execution: list[str],
) -> None:
    container = make_container()
    approval_id = _create(container)

    response = _call(container, "GET", f"/approvals/{approval_id}", token=_token("human-1"))

    assert response.status_code == 404
    assert _state(engine, approval_id) == ("pending", 1, [])
    assert no_execution == []


def test_production_approval_persistence_gate_stays_closed() -> None:
    issues = collect_config_issues(Settings(**HARDENED_PRODUCTION))

    assert APPROVAL_PERSISTENCE_ISSUE in {issue.setting for issue in issues}
