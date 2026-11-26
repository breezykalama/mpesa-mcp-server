"""Unit tests for provider evidence verification contracts and correlation (Slice E2.2a).

The correlation component is pure: these tests need no database and no provider.
"""

from __future__ import annotations

import ast
import dataclasses
import itertools
from pathlib import Path
from typing import Any

import pytest
from app.main import app
from app.payments.provider_verification import (
    SOURCE_CAPABILITIES,
    AttemptBinding,
    AttemptCorrelationAssessment,
    AttemptCorrelationContext,
    CorrelationResult,
    ProviderIdentity,
    ProviderObservation,
    assess_attempt_correlation,
    correlate_observation,
)
from app.policy.tool_policy import AGENT_FORBIDDEN_TOOLS, CURRENT_MCP_TOOLS

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BINDING = AttemptBinding(
    attempt_id="attempt-1", claim_id="claim-1", approval_id="approval-1", payload_hash="a" * 64
)
OTHER_BINDING = AttemptBinding(
    attempt_id="attempt-2", claim_id="claim-2", approval_id="approval-2", payload_hash="b" * 64
)
CHECKOUT = "ws_CO_191220191020363925"
MERCHANT = "29115-34620561-1"
RECEIPT = "NLJ7RT61SV"
DARAJA_MPESA = ProviderIdentity()


def _context(
    binding: AttemptBinding = BINDING,
    *,
    checkout: frozenset[str] = frozenset({CHECKOUT}),
    merchant: frozenset[str] = frozenset({MERCHANT}),
    identity: ProviderIdentity = DARAJA_MPESA,
) -> AttemptCorrelationContext:
    return AttemptCorrelationContext(
        binding=binding,
        identity=identity,
        checkout_request_ids=checkout,
        merchant_request_ids=merchant,
    )


def _callback(**overrides: Any) -> ProviderObservation:
    values: dict[str, Any] = {
        "source": "provider_callback",
        "result_code": "0",
        "checkout_request_id": CHECKOUT,
        "merchant_request_id": MERCHANT,
        "settlement_id": RECEIPT,
    }
    values.update(overrides)
    return ProviderObservation(**values)


def _initiation(**overrides: Any) -> ProviderObservation:
    values: dict[str, Any] = {
        "source": "provider_initiation_response",
        "result_code": "0",
        "checkout_request_id": CHECKOUT,
        "merchant_request_id": MERCHANT,
        # The existing adapter repeats the CheckoutRequestID as provider_transaction_id.
        "settlement_id": CHECKOUT,
    }
    values.update(overrides)
    return ProviderObservation(**values)


def _status_query(**overrides: Any) -> ProviderObservation:
    values: dict[str, Any] = {
        "source": "provider_status_query",
        "result_code": "0",
        "checkout_request_id": CHECKOUT,
        "settlement_id": CHECKOUT,
    }
    values.update(overrides)
    return ProviderObservation(**values)


def _assert_never_an_outcome(result: CorrelationResult | AttemptCorrelationAssessment) -> None:
    assert result.completion_established is False
    assert result.eligibility.eligible is False
    assert result.eligibility.reasons


# --- Single-observation correlation ----------------------------------------------------


def test_valid_single_attempt_correlation() -> None:
    result = correlate_observation(_callback(), [_context()])

    assert result.status == "correlated"
    assert result.reasons == ("matched_checkout_request_id",)
    assert result.attempt == BINDING
    _assert_never_an_outcome(result)


@pytest.mark.parametrize("checkout", [None, ""])
def test_missing_checkout_request_id_is_uncorrelated(checkout: str | None) -> None:
    result = correlate_observation(_callback(checkout_request_id=checkout), [_context()])

    assert result.status == "uncorrelated"
    assert result.reasons == ("missing_checkout_request_id",)
    assert result.attempt is None
    assert result.eligibility.reasons == ("not_correlated",)


def test_merchant_request_id_alone_never_correlates() -> None:
    observation = _callback(checkout_request_id=None, merchant_request_id=MERCHANT)

    assert correlate_observation(observation, [_context()]).status == "uncorrelated"


@pytest.mark.parametrize(
    "checkout",
    ["ws_CO_000000000000000000", f" {CHECKOUT}", f"{CHECKOUT} ", CHECKOUT.lower()],
    ids=["different", "leading-space", "trailing-space", "re-cased"],
)
def test_incorrect_checkout_request_id_is_not_corrected(checkout: str) -> None:
    result = correlate_observation(_callback(checkout_request_id=checkout), [_context()])

    assert result.status == "uncorrelated"
    assert result.reasons == ("no_matching_attempt",)


def test_merchant_request_id_mismatch_conflicts() -> None:
    result = correlate_observation(_callback(merchant_request_id="other-merchant"), [_context()])

    assert result.status == "conflicting"
    assert result.reasons == ("merchant_request_id_mismatch",)
    assert result.attempt == BINDING
    _assert_never_an_outcome(result)


def test_missing_merchant_request_id_still_correlates_on_checkout() -> None:
    result = correlate_observation(_callback(merchant_request_id=None), [_context()])

    assert result.status == "correlated"


def test_ambiguous_match_is_refused() -> None:
    result = correlate_observation(_callback(), [_context(), _context(OTHER_BINDING)])

    assert result.status == "ambiguous"
    assert result.reasons == ("multiple_matching_attempts",)
    assert result.attempt is None


@pytest.mark.parametrize(
    "binding",
    [
        OTHER_BINDING,
        dataclasses.replace(BINDING, approval_id="approval-2"),
        dataclasses.replace(BINDING, claim_id="claim-2"),
        dataclasses.replace(BINDING, payload_hash="c" * 64),
    ],
    ids=["other-attempt", "other-approval", "other-claim", "other-payload-hash"],
)
def test_wrong_approval_or_claim_binding_conflicts(binding: AttemptBinding) -> None:
    result = correlate_observation(_callback(binding=binding), [_context()])

    assert result.status == "conflicting"
    assert result.reasons == ("binding_mismatch",)


def test_provider_or_rail_mismatch_is_uncorrelated() -> None:
    for identity in (ProviderIdentity(provider="other"), ProviderIdentity(rail="card")):
        result = correlate_observation(_callback(identity=identity), [_context()])
        assert result.status == "uncorrelated"
        assert result.reasons == ("provider_or_rail_mismatch",)


def test_identifier_reused_across_approvals_is_ambiguous_not_guessed() -> None:
    # There is no tenant model; two approvals sharing a request identifier must never
    # be resolved by picking one.
    contexts = [_context(), _context(OTHER_BINDING)]

    for observation in (_callback(), _initiation(), _status_query()):
        assert correlate_observation(observation, contexts).status == "ambiguous"


def test_attempt_without_owner_initiation_identifiers_is_unresolved() -> None:
    context = _context(checkout=frozenset(), merchant=frozenset())

    result = correlate_observation(_callback(), [context])

    assert result.status == "uncorrelated"
    assert result.reasons == ("attempt_initiation_identifiers_unknown",)


def test_attempt_with_conflicting_initiation_identifiers_conflicts() -> None:
    context = _context(checkout=frozenset({CHECKOUT, "ws_CO_second"}))

    result = correlate_observation(_callback(), [context])

    assert result.status == "conflicting"
    assert "attempt_initiation_identifiers_conflict" in result.reasons


@pytest.mark.parametrize("make", [_initiation, _status_query], ids=["initiation", "status-query"])
def test_non_callback_source_cannot_carry_a_settlement_identifier(make: Any) -> None:
    assert correlate_observation(make(), [_context()]).status == "correlated"  # alias only

    result = correlate_observation(make(settlement_id=RECEIPT), [_context()])

    assert result.status == "conflicting"
    assert result.reasons == ("non_settlement_source_carries_settlement_identifier",)


def test_unsupported_source_is_refused() -> None:
    with pytest.raises(ValueError, match="Unsupported observation source"):
        ProviderObservation(source="execution_worker")  # type: ignore[arg-type]


# --- Trust boundaries --------------------------------------------------------------------


def test_no_source_is_authoritative_for_completion() -> None:
    assert set(SOURCE_CAPABILITIES) == {
        "provider_initiation_response",
        "provider_callback",
        "provider_status_query",
    }
    for capability in SOURCE_CAPABILITIES.values():
        assert capability.authoritative_for_completion is False
        assert "no_authoritative_verification_mechanism" in capability.ineligibility


def test_unverified_callback_is_correlated_but_not_eligible() -> None:
    result = correlate_observation(_callback(), [_context()])

    assert result.source_capability.authentication == "sender_authenticated_not_provider"
    assert result.source_capability.establishes == "reported_result_unauthenticated"
    assert "source_not_provider_authenticated" in result.eligibility.reasons
    _assert_never_an_outcome(result)


def test_initiation_acknowledgment_is_not_completion() -> None:
    result = correlate_observation(_initiation(result_code="0"), [_context()])
    assessment = assess_attempt_correlation(_context(), [_initiation(result_code="0")])

    assert result.status == "correlated"
    assert result.source_capability.establishes == "request_acknowledgment"
    assert "source_establishes_acknowledgment_only" in result.eligibility.reasons
    assert assessment.status == "correlated"
    assert assessment.settlement_ids == frozenset()
    _assert_never_an_outcome(result)
    _assert_never_an_outcome(assessment)


def test_status_query_response_is_query_acceptance_only() -> None:
    result = correlate_observation(_status_query(), [_context()])

    assert result.source_capability.establishes == "query_acceptance_only"
    assert "status_query_result_not_ingested" in result.eligibility.reasons
    _assert_never_an_outcome(result)


def test_results_expose_no_payment_outcome_field() -> None:
    for model in (CorrelationResult, AttemptCorrelationAssessment):
        names = {item.name for item in dataclasses.fields(model)}
        assert not names & {"success", "succeeded", "paid", "verified", "payment_status"}


# --- Attempt-level assessment ------------------------------------------------------------


def test_duplicate_observations_collapse() -> None:
    assessment = assess_attempt_correlation(_context(), [_callback()] * 3 + [_initiation()] * 2)

    assert assessment.status == "correlated"
    assert assessment.distinct_observations == 2
    assert assessment.duplicate_observations == 3
    assert assessment.settlement_ids == frozenset({RECEIPT})
    assert assessment.sources == frozenset({"provider_callback", "provider_initiation_response"})
    _assert_never_an_outcome(assessment)


def test_repeated_callbacks_are_not_independent_corroboration() -> None:
    assessment = assess_attempt_correlation(_context(), [_callback(), _callback()])

    assert assessment.sources == frozenset({"provider_callback"})
    assert assessment.distinct_observations == 1


def test_assessment_is_independent_of_arrival_order() -> None:
    observations = [
        _initiation(),
        _callback(),
        _status_query(),
        _callback(settlement_id="OTHERRCPT"),
    ]

    results = {
        assess_attempt_correlation(_context(), list(order))
        for order in itertools.permutations(observations)
    }

    assert len(results) == 1
    assert next(iter(results)).status == "conflicting"


def test_conflicting_settlement_identifiers() -> None:
    assessment = assess_attempt_correlation(
        _context(), [_callback(), _callback(settlement_id="OTHERRCPT")]
    )

    assert assessment.status == "conflicting"
    assert "conflicting_settlement_identifiers" in assessment.reasons
    assert assessment.settlement_ids == frozenset({RECEIPT, "OTHERRCPT"})


def test_conflicting_callback_result_codes() -> None:
    assessment = assess_attempt_correlation(
        _context(), [_callback(), _callback(result_code="1032", settlement_id=None)]
    )

    assert assessment.status == "conflicting"
    assert "conflicting_callback_result_codes" in assessment.reasons


def test_callback_after_rejected_initiation_conflicts() -> None:
    assessment = assess_attempt_correlation(_context(), [_initiation(result_code="1"), _callback()])

    assert assessment.status == "conflicting"
    assert "callback_after_rejected_initiation" in assessment.reasons


def test_observation_for_a_different_request_conflicts() -> None:
    assessment = assess_attempt_correlation(
        _context(), [_callback(), _callback(checkout_request_id="ws_CO_other")]
    )

    assert assessment.status == "conflicting"
    assert "observation_for_different_request" in assessment.reasons


def test_no_or_unusable_observations_are_unresolved() -> None:
    empty = assess_attempt_correlation(_context(), [])
    missing = assess_attempt_correlation(_context(), [_callback(checkout_request_id=None)])

    assert empty.status == "unresolved"
    assert empty.reasons == ("no_observations",)
    assert missing.status == "unresolved"
    assert missing.reasons == ("missing_checkout_request_id",)
    for assessment in (empty, missing):
        _assert_never_an_outcome(assessment)


# --- Isolation ---------------------------------------------------------------------------


def test_module_is_pure_and_not_exposed() -> None:
    source = REPOSITORY_ROOT / "app" / "payments" / "provider_verification.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert imported <= {
        "__future__",
        "collections.abc",
        "dataclasses",
        "typing",
        "app.payments.execution_evidence",
    }

    tool_names = {*CURRENT_MCP_TOOLS, *AGENT_FORBIDDEN_TOOLS}
    assert not any("verif" in name or "correlat" in name for name in tool_names)
    assert not any(
        "verif" in getattr(route, "path", "") or "correlat" in getattr(route, "path", "")
        for route in app.routes
    )
    for path in (REPOSITORY_ROOT / "app").rglob("*.py"):
        if path.name == "provider_verification.py":
            continue
        assert "provider_verification" not in path.read_text(encoding="utf-8"), path
