"""PostgreSQL tests: correlating real E2.1 evidence records (Slice E2.2a).

Correlation reads E2.1 attempts and evidence and never writes. These tests prove that
contexts are built only from owner-recorded initiation evidence, that stored
observations correlate to their own attempt and conflict with any other, that E2.1
rows are unchanged afterwards, and that no payment provider is called. Fixtures live in
``tests/integration/conftest.py``; tests skip unless ``MPESA_TEST_POSTGRES_URL`` points
at a local disposable ``*_test`` database.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from app.approvals.postgres_repository import PostgresApprovalRepository
from app.daraja.client import MockDarajaClient, RealDarajaClient
from app.payments.execution_claims import ClaimOutcome, PostgresExecutionClaimRepository
from app.payments.execution_evidence import (
    ExecutionAttemptRecord,
    PostgresExecutionEvidenceRepository,
)
from app.payments.provider_verification import (
    AttemptCorrelationContext,
    ProviderIdentity,
    assess_attempt_correlation,
    correlate_observation,
    observation_from_evidence,
)
from app.payments.providers import DarajaPaymentProvider
from app.services.payment_service import PaymentService
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker
from tests.identity_fixtures import VERIFIED_TEST_ISSUER, verified_human

PAYLOAD: dict[str, Any] = {
    "phone_number": "254700000000",
    "amount": 75_000,
    "account_reference": "INV-E22A",
    "description": "Supplier settlement",
    "idempotency_key": "idem-e22a",
}


@pytest.fixture(autouse=True)
def no_payment_provider(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def forbidden(name: str) -> Callable[..., Any]:
        def _call(*_args: object, **_kwargs: object) -> Any:
            calls.append(name)
            raise AssertionError(f"provider path must not be called: {name}")

        return _call

    for owner, attribute in (
        (DarajaPaymentProvider, "initiate_payment"),
        (DarajaPaymentProvider, "check_transaction_status"),
        (MockDarajaClient, "initiate_stk_push"),
        (MockDarajaClient, "check_transaction_status"),
        (RealDarajaClient, "initiate_stk_push"),
        (RealDarajaClient, "check_transaction_status"),
        (PaymentService, "execute_approved_payment"),
        (PaymentService, "initiate_stk_push"),
    ):
        monkeypatch.setattr(owner, attribute, forbidden(f"{owner.__name__}.{attribute}"))
    return calls


@pytest.fixture
def repositories(
    session_factory: sessionmaker[Session],
) -> tuple[
    PostgresApprovalRepository,
    PostgresExecutionClaimRepository,
    PostgresExecutionEvidenceRepository,
]:
    return (
        PostgresApprovalRepository(
            session_factory, trusted_reviewer_issuers=frozenset({VERIFIED_TEST_ISSUER})
        ),
        PostgresExecutionClaimRepository(session_factory, worker_id="worker-e22a"),
        PostgresExecutionEvidenceRepository(session_factory),
    )


def _attempt(
    repositories: tuple[Any, Any, Any], checkout: str
) -> tuple[ClaimOutcome, ExecutionAttemptRecord]:
    approvals, claims, evidence = repositories
    with approvals._session_factory() as session:  # noqa: SLF001 - database clock
        now = session.execute(text("SELECT clock_timestamp()")).scalar_one()
    record = approvals.create(
        action="initiate_stk_push",
        payload=PAYLOAD,
        reason="High-value payment requires approval",
        created_at=now - timedelta(hours=1),
        expires_at=now + timedelta(minutes=30),
        required_reviewers=1,
        policy_version="approval-policy/v1",
        policy_snapshot={"required_reviewers": 1},
    )
    approval_id = record.approval.approval_id
    approvals.record_decision(
        approval_id, reviewer=verified_human(f"approver-{uuid4()}"), decision="approve"
    )
    claim = claims.claim_execution(approval_id)
    owner = claim.claim_token
    attempt = evidence.record_attempt(claim.claim.claim_id, claim_token=owner)
    evidence.record_submission_started(attempt.attempt_id, claim_token=owner)
    evidence.record_provider_observation(
        attempt.attempt_id,
        source="provider_initiation_response",
        observation_key="initiation",
        result_code="0",
        checkout_request_id=checkout,
        merchant_request_id=f"mr-{checkout}",
        provider_transaction_id=checkout,  # what the existing adapter stores here
        claim_token=owner,
    )
    return claim, attempt


def _snapshot(engine: Engine) -> tuple[Any, ...]:
    with engine.connect() as connection:
        return tuple(
            tuple(tuple(row) for row in connection.execute(text(query)))
            for query in (
                "SELECT * FROM payment_execution_attempts ORDER BY attempt_id",
                "SELECT * FROM payment_execution_evidence ORDER BY evidence_id",
                "SELECT * FROM payment_execution_claims ORDER BY claim_id",
            )
        )


def test_stored_observations_correlate_only_to_their_own_attempt(
    repositories: tuple[Any, Any, Any],
    engine: Engine,
    no_payment_provider: list[str],
) -> None:
    _, _, evidence = repositories
    checkout_a = f"ws_CO_{uuid4().hex[:12]}"
    checkout_b = f"ws_CO_{uuid4().hex[:12]}"
    claim_a, attempt_a = _attempt(repositories, checkout_a)
    claim_b, attempt_b = _attempt(repositories, checkout_b)
    evidence.record_provider_observation(
        attempt_a.attempt_id,
        source="provider_callback",
        observation_key="callback-1",
        result_code="0",
        checkout_request_id=checkout_a,
        merchant_request_id=f"mr-{checkout_a}",
        provider_transaction_id="RCPTE22A",
    )
    before = _snapshot(engine)

    records_a = evidence.list_evidence(claim_a.claim.claim_id)
    records_b = evidence.list_evidence(claim_b.claim.claim_id)
    context_a = AttemptCorrelationContext.from_records(attempt_a, records_a)
    context_b = AttemptCorrelationContext.from_records(attempt_b, records_b)
    identity = ProviderIdentity(provider=attempt_a.provider, rail=attempt_a.rail)
    observations_a = [
        observation_from_evidence(row, identity=identity)
        for row in records_a
        if row.evidence_type == "provider_response_observed"
    ]

    assert context_a.checkout_request_id == checkout_a
    assert context_b.checkout_request_id == checkout_b
    for observation in observations_a:
        result = correlate_observation(observation, [context_a, context_b])
        assert result.status == "correlated", result
        assert result.attempt == context_a.binding
        assert result.completion_established is False
        assert result.eligibility.eligible is False
        # Against the other attempt alone it never correlates.
        assert correlate_observation(observation, [context_b]).status != "correlated"

    assessment = assess_attempt_correlation(context_a, observations_a)
    assert assessment.status == "correlated"
    assert assessment.settlement_ids == frozenset({"RCPTE22A"})
    assert assessment.completion_established is False

    # A stored observation presented against another attempt's identity conflicts.
    forged = observation_from_evidence(records_a[-1], identity=identity)
    other = AttemptCorrelationContext(
        binding=context_b.binding,
        identity=context_b.identity,
        checkout_request_ids=frozenset({checkout_a}),
        merchant_request_ids=context_a.merchant_request_ids,
    )
    assert correlate_observation(forged, [other]).reasons == ("binding_mismatch",)

    with pytest.raises(ValueError, match="does not belong"):
        AttemptCorrelationContext.from_records(attempt_b, records_a)

    assert _snapshot(engine) == before
    assert no_payment_provider == []


def test_callback_alone_never_establishes_attempt_identifiers(
    repositories: tuple[Any, Any, Any],
    engine: Engine,
) -> None:
    approvals, claims, evidence = repositories
    claim, attempt = _attempt(repositories, f"ws_CO_{uuid4().hex[:12]}")
    # A second attempt whose owner never recorded an initiation response.
    with approvals._session_factory() as session:  # noqa: SLF001
        now = session.execute(text("SELECT clock_timestamp()")).scalar_one()
    bare = approvals.create(
        action="initiate_stk_push",
        payload=PAYLOAD,
        reason="High-value payment requires approval",
        created_at=now - timedelta(hours=1),
        expires_at=now + timedelta(minutes=30),
        required_reviewers=1,
        policy_version="approval-policy/v1",
        policy_snapshot={"required_reviewers": 1},
    )
    approvals.record_decision(
        bare.approval.approval_id, reviewer=verified_human(f"a-{uuid4()}"), decision="approve"
    )
    bare_claim = claims.claim_execution(bare.approval.approval_id)
    owner = bare_claim.claim_token
    bare_attempt = evidence.record_attempt(bare_claim.claim.claim_id, claim_token=owner)
    evidence.record_submission_started(bare_attempt.attempt_id, claim_token=owner)
    evidence.record_provider_observation(
        bare_attempt.attempt_id,
        source="provider_callback",
        observation_key="callback-1",
        result_code="0",
        checkout_request_id="ws_CO_unestablished",
    )
    before = _snapshot(engine)

    records = evidence.list_evidence(bare_claim.claim.claim_id)
    context = AttemptCorrelationContext.from_records(bare_attempt, records)
    identity = ProviderIdentity()
    callback = observation_from_evidence(records[-1], identity=identity)

    assert context.checkout_request_ids == frozenset()
    result = correlate_observation(callback, [context])
    assert result.status == "uncorrelated"
    assert result.reasons == ("attempt_initiation_identifiers_unknown",)
    assert assess_attempt_correlation(context, [callback]).status == "unresolved"
    with pytest.raises(ValueError, match="Only provider observations"):
        observation_from_evidence(records[0], identity=identity)
    assert _snapshot(engine) == before
    assert claim.claim.status == "claimed"
